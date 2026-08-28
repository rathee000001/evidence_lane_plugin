"""Composition root and canonical tool-result contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from .adaptive_delta_entry import run_adaptive_delta_entry
from .adaptive_delta_exit import run_adaptive_delta_exit
from .agent_configuration import AgentConfigurationManager
from .agent_learning import inspect_learning_authority
from .capture_routing import CaptureRouteAuthority
from .connector_governance import ConnectorGovernance
from .constants import LIFECYCLE_RESULT_SCHEMA, TOOL_RESULT_SCHEMA
from .conversation_memory import ConversationMemoryManager
from .custom_source_schema import (
    compile_and_map_custom_source_schema,
    configure_source_intake_schema_pill,
)
from .engine import CodePVEngine
from .engine_identity import identity_repository_root
from .enrollment import enroll_project, sync_selected_branch
from .errors import EvidenceLaneError, require
from .flash_authority import SessionFlashAuthority
from .freshness import evaluate_freshness, evaluate_working_lane_freshness
from .git_adapter import (
    calculate_worktree_change_identity,
    calculate_worktree_sha256,
    inspect_repository,
)
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .hil_intent import classify_hil_intent
from .host_entry_continuity import (
    TransactionalHostEntryBackend,
    consume_host_entry_envelope,
    derive_host_entry_authority_heads,
    derive_host_entry_env_uop,
)
from .host_plan_rehydration import _exact_projection
from .ids import prefixed_id
from .lane_reader import LaneReader
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from .lineage import ProjectChatLineage
from .model_compatibility import model_compatibility_catalog
from .models import ProjectConfig, SessionState, normalize_host_kind
from .next_actions import HIL_CHOICES, HIL_SUGGESTED_PROMPT
from .operating_modes import classify_operating_modes
from .persistence import (
    GoogleDrivePersistence,
    PersistenceRoute,
    PVSyncService,
    route_persistence,
)
from .project_authority import (
    migrate_working_project_sectors,
    query_working_project_sectors,
    resolved_chat_lineage_root,
)
from .prompt_index import PromptIndex
from .pv_package import validate_pv_package
from .reader import PVReader
from .redaction import redact
from .remote_git import RemoteGitController
from .runtime_activation import RuntimeActivation
from .session import SessionManager
from .source_fingerprint import tracked_worktree_file_manifest
from .source_git_history import (
    build_registered_git_history,
    build_source_git_commit_impact,
)
from .source_graph import (
    build_registered_source_graph,
    diff_source_graphs,
    source_graph_impact,
)
from .source_identity import register_source_identity_matrix
from .source_intake import classify_source_intake
from .source_sqlite import inspect_registered_sqlite_assets
from .state_law import transition_catalog
from .state_travel_contract import preflight_direct_forced_same_worktree_binding
from .storage_selection import StorageSelection
from .store import ProjectStore
from .timeutil import utc_now

_STATUS_VALIDATION_WORKERS = 8

# Public tool results are copied into the host conversation by the MCP SDK.  A
# result that is safe inside the private service can therefore still be unsafe
# at the public boundary (for example, a successful Plan write historically
# returned the complete canonical and executable projections).  Keep one hard
# byte ceiling here so MCP, SDK, command, and skill routes share the same
# fail-closed model-context contract.
_MODEL_CONTEXT_RESULT_MAX_BYTES = 64 * 1024
_MODEL_CONTEXT_DATA_MAX_BYTES = 48 * 1024
_MODEL_CONTEXT_AUXILIARY_MAX_BYTES = 4 * 1024
_MODEL_CONTEXT_SCALAR_MAX_BYTES = 8 * 1024
_MODEL_CONTEXT_COLLECTION_MAX_ITEMS = 50
_MODEL_CONTEXT_MAPPING_MAX_ITEMS = 128
_MODEL_CONTEXT_MAX_DEPTH = 8
_MODEL_CONTEXT_APPROX_TOKEN_MAX = 16 * 1024
_MODEL_CONTEXT_BOUNDARY_SCHEMA = "evidence-lane.model-context-boundary.v1"
_MODEL_CONTEXT_WITHHELD_SCHEMA = "evidence-lane.model-context-withheld-receipt.v1"
_MODEL_CONTEXT_AUXILIARY_WITHHELD_SCHEMA = (
    "evidence-lane.model-context-auxiliary-withheld-receipt.v1"
)
_MODEL_CONTEXT_DETAIL_ROUTES = [
    "pv_status",
    "pv_task_backlog",
    "pv_query",
    "search",
    "fetch",
    "lane_search",
]
_MODEL_CONTEXT_AUTHORITY_KEYS = frozenset(
    {
        "backlog",
        "canonical_plan_projection",
        "full_backlog",
        "full_chatlineage",
        "full_chat_scrollback",
        "full_plan",
        "goal_projection",
        "history_projection",
        "plan_runtime_projection",
        "raw_file",
        "raw_markdown",
        "raw_package_projection",
        "raw_pv_payload",
        "raw_sqlite",
    }
)


def _bounded_value_receipt(
    value: object,
    *,
    reason: str,
) -> dict[str, Any]:
    """Describe one withheld nested value without copying it into context."""

    encoded = canonical_json_bytes(value)
    return {
        "schema": _MODEL_CONTEXT_WITHHELD_SCHEMA,
        "payload_withheld": True,
        "reason": reason,
        "result_bytes": len(encoded),
        "result_sha256": sha256_bytes(encoded),
        "raw_payload_returned": False,
    }


def _bound_nested_public_value(
    value: object,
    *,
    path: tuple[str, ...] = (),
    depth: int = 0,
) -> object:
    """Bound nested values before the complete-envelope byte ceiling runs.

    Public reads may return an exact excerpt, row, or FTS window.  They may not
    return an authority database, recursive history, or arbitrarily large
    scalar merely because the complete result happens to remain below a later
    transport limit.
    """

    if depth > _MODEL_CONTEXT_MAX_DEPTH:
        return _bounded_value_receipt(value, reason="MAX_DEPTH_EXCEEDED")
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        entries = list(value.items())
        for raw_key, nested in entries[:_MODEL_CONTEXT_MAPPING_MAX_ITEMS]:
            key = str(raw_key)
            if key.lower() in _MODEL_CONTEXT_AUTHORITY_KEYS:
                bounded[key] = _bounded_value_receipt(
                    nested,
                    reason="FULL_AUTHORITY_FIELD_BLOCKED",
                )
                continue
            bounded[key] = _bound_nested_public_value(
                nested,
                path=(*path, key),
                depth=depth + 1,
            )
        if len(entries) > _MODEL_CONTEXT_MAPPING_MAX_ITEMS:
            omitted = dict(entries[_MODEL_CONTEXT_MAPPING_MAX_ITEMS:])
            bounded["_withheld_mapping_tail"] = _bounded_value_receipt(
                omitted,
                reason="MAPPING_ITEM_LIMIT_EXCEEDED",
            )
            bounded["_withheld_mapping_tail"]["omitted_items"] = (
                len(entries) - _MODEL_CONTEXT_MAPPING_MAX_ITEMS
            )
        return bounded
    if isinstance(value, list):
        bounded_items = [
            _bound_nested_public_value(
                item,
                path=(*path, str(index)),
                depth=depth + 1,
            )
            for index, item in enumerate(value[:_MODEL_CONTEXT_COLLECTION_MAX_ITEMS])
        ]
        if len(value) > _MODEL_CONTEXT_COLLECTION_MAX_ITEMS:
            tail = value[_MODEL_CONTEXT_COLLECTION_MAX_ITEMS:]
            receipt = _bounded_value_receipt(
                tail,
                reason="COLLECTION_ITEM_LIMIT_EXCEEDED",
            )
            receipt["omitted_items"] = len(tail)
            bounded_items.append(receipt)
        return bounded_items
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) <= _MODEL_CONTEXT_SCALAR_MAX_BYTES:
            return value
        prefix = encoded[:_MODEL_CONTEXT_SCALAR_MAX_BYTES].decode(
            "utf-8", errors="ignore"
        )
        return (
            prefix
            + "\n[MODEL_CONTEXT_TRUNCATED bytes="
            + str(len(encoded))
            + " sha256="
            + sha256_bytes(encoded)
            + "]"
        )
    return value


def _compact_fetch_result(data: dict[str, Any]) -> dict[str, Any]:
    """Keep one exact fetch excerpt and remove the duplicate content field."""

    bounded = dict(data)
    text = bounded.get("text")
    content = bounded.get("content")
    if isinstance(text, str) and content == text:
        bounded.pop("content", None)
        bounded["content_duplicate_returned"] = False
    bounded["model_context_boundary"] = {
        "schema": _MODEL_CONTEXT_BOUNDARY_SCHEMA,
        "exact_bounded_read": True,
        "full_file_returned": False,
        "raw_database_returned": False,
        "duplicate_content_returned": False,
        "max_scalar_bytes": _MODEL_CONTEXT_SCALAR_MAX_BYTES,
    }
    return bounded


def _authority_field_paths(
    value: object,
    *,
    path: tuple[str, ...] = (),
    depth: int = 0,
) -> list[str]:
    """Locate forbidden authority payload fields without reading their values."""

    if depth > _MODEL_CONTEXT_MAX_DEPTH:
        return []
    if isinstance(value, dict):
        found: list[str] = []
        for raw_key, nested in list(value.items())[:_MODEL_CONTEXT_MAPPING_MAX_ITEMS]:
            key = str(raw_key)
            exact_path = (*path, key)
            if key.lower() in _MODEL_CONTEXT_AUTHORITY_KEYS:
                found.append(".".join(exact_path))
            else:
                found.extend(
                    _authority_field_paths(
                        nested,
                        path=exact_path,
                        depth=depth + 1,
                    )
                )
            if len(found) >= 32:
                return found[:32]
        return found
    if isinstance(value, list):
        found = []
        for index, nested in enumerate(value[:_MODEL_CONTEXT_COLLECTION_MAX_ITEMS]):
            found.extend(
                _authority_field_paths(
                    nested,
                    path=(*path, str(index)),
                    depth=depth + 1,
                )
            )
            if len(found) >= 32:
                return found[:32]
        return found
    return []


def _compact_fields(
    payload: object,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    """Copy only explicitly public receipt fields from one mapping."""

    if not isinstance(payload, dict):
        return {}
    return {field: payload[field] for field in fields if field in payload}


def _compact_plan_tasks_result(data: dict[str, Any]) -> dict[str, Any]:
    """Project one Plan mutation to a receipt, never the full Plan authority."""

    goal = cast(dict[str, Any], data.get("goal_projection") or {})
    rows = cast(list[dict[str, Any]], goal.get("rows") or [])
    active_rows = [row for row in rows if str(row.get("status")) == "in_progress"]
    final_rows = [
        row
        for row in rows
        if str(row.get("panel_role") or "") == "PHYSICALLY_FINAL_HIL"
    ]
    active = active_rows[0] if len(active_rows) == 1 else {}
    physical_final = final_rows[-1] if final_rows else {}
    runtime = cast(dict[str, Any], data.get("plan_runtime_projection") or {})
    history = cast(dict[str, Any], data.get("history_projection") or {})
    canonical = cast(dict[str, Any], data.get("canonical_plan_projection") or {})
    host = cast(dict[str, Any], data.get("host_plan_bridge") or {})

    receipt_fields = (
        "schema",
        "status",
        "project_id",
        "plan_id",
        "batch_id",
        "research_batch_sha256",
        "task_count",
        "group_count",
        "task_ids",
        "physical_final_task_id",
        "physical_final_sequence",
        "canonical_plan_sha256",
        "executable_projection_sha256",
        "backlog_sha256_before",
        "backlog_sha256_after",
        "result_sha256",
        "receipt_sha256",
        "idempotent_replay",
        "recovered_prepared_insertion",
    )
    priority_fields = (
        "schema",
        "status",
        "interruption_id",
        "session_id",
        "old_active_task_id",
        "replacement_task_id",
        "paused_backlog_task_id",
        "replacement_backlog_task_id",
        "history_preserved",
        "candidate_created",
        "pending_hil",
        "pointer_moved",
        "receipt_sha256",
        "idempotent_replay",
    )
    host_fields = (
        "host_kind",
        "host_mode",
        "canonical_authority",
        "copy_paste_required",
        "host_goal_mutation_supported_by_mcp",
        "host_scope",
    )
    return {
        "schema": "evidence-lane.plan-write-receipt.v2",
        "status": str(data.get("status") or "PASS"),
        "project_id": data.get("project_id") or goal.get("project_id"),
        "write_committed": str(data.get("status") or "PASS") == "PASS",
        "counts": data.get("counts"),
        "canonical_task_count": (
            goal.get("canonical_task_count") or canonical.get("task_count")
        ),
        "executable_task_count": goal.get("task_count"),
        "history_task_count": (
            goal.get("history_task_count") or history.get("task_count")
        ),
        "row_start": goal.get("row_start"),
        "row_end": goal.get("row_end"),
        "active_row": (
            {
                "number": active.get("number"),
                "task_id": active.get("task_id"),
                "status": active.get("status"),
                "lifecycle_status": active.get("lifecycle_status"),
            }
            if active
            else None
        ),
        "physical_final_hil": (
            {
                "number": physical_final.get("number"),
                "task_id": physical_final.get("task_id"),
                "status": physical_final.get("status"),
            }
            if physical_final
            else None
        ),
        "canonical_plan_sha256": (
            goal.get("canonical_plan_sha256") or canonical.get("projection_sha256")
        ),
        "executable_projection_sha256": goal.get("projection_sha256"),
        "history_projection_sha256": goal.get("history_projection_sha256"),
        "plan_runtime_receipt": {
            "status": runtime.get("status"),
            "sqlite_sha256": runtime.get("sqlite_sha256"),
            "projection_content_sha256": runtime.get("projection_content_sha256"),
            "execution_row_count": runtime.get("execution_row_count"),
            "history_row_count": runtime.get("history_row_count"),
            "fts_record_count": runtime.get("fts_record_count"),
            "fts5_enabled": runtime.get("fts5_enabled"),
        },
        "atomic_insertion_receipt": _compact_fields(
            data.get("atomic_insertion_receipt"), receipt_fields
        ),
        "priority_steer_receipt": _compact_fields(
            data.get("priority_steer_receipt"), priority_fields
        ),
        "priority_steer_rebind": _compact_fields(
            data.get("priority_steer_rebind"), priority_fields
        ),
        "normalization_transition_receipt": _compact_fields(
            data.get("normalization_transition_receipt"), receipt_fields
        ),
        "active_contract_rebind_receipt": _compact_fields(
            data.get("active_contract_rebind_receipt"), receipt_fields
        ),
        "host_plan_bridge": _compact_fields(host, host_fields),
        "model_context_boundary": {
            "schema": _MODEL_CONTEXT_BOUNDARY_SCHEMA,
            "full_backlog_returned": False,
            "full_plan_returned": False,
            "raw_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
            "write_receipt_only": True,
            "detail_routes": ["pv_status", "pv_task_backlog"],
        },
    }


def _compact_plan_steer_result(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the public receipt for one steer, even after future drift."""

    steer_fields = (
        "delta_id",
        "delta_sha256",
        "boundary",
        "boundary_defaulted",
        "classification",
        "linked_task_id",
        "recorded_by",
        "delta_text_returned",
    )
    event_fields = (
        "sequence",
        "event_id",
        "task_id",
        "event_type",
        "from_status",
        "to_status",
        "actor",
        "recorded_at",
        "event_sha256",
        "details",
    )
    window_fields = (
        "schema",
        "window_size",
        "row_start",
        "row_end",
        "linked_task_id",
        "active_row_present",
        "linked_row_is_currently_visible",
        "before_fingerprint_sha256",
        "after_fingerprint_sha256",
        "visible_window_changed",
        "action",
        "host_update_plan_required",
        "evi_refresh_invoked",
        "full_native_ledger_remains_authority",
        "text_only_linked_steer_rebuilds_host_window",
    )
    backlog_fields = (
        "schema",
        "project_id",
        "canonical_task_count",
        "executable_task_count",
        "history_task_count",
        "counts",
        "active_task_id",
        "absolute_active_row",
        "canonical_plan_sha256",
        "executable_projection_sha256",
        "plan_runtime_sqlite_sha256",
        "plan_runtime_projection_content_sha256",
        "full_backlog_returned",
        "full_plan_returned",
        "raw_pv_payload_loaded",
        "raw_chat_scrollback_loaded",
    )
    return {
        "schema": "evidence-lane.plan-steer-write-receipt.v2",
        "status": str(data.get("status") or "PASS"),
        "idempotent_reuse": bool(data.get("idempotent_reuse")),
        "steer": _compact_fields(data.get("steer"), steer_fields),
        "event": _compact_fields(data.get("event"), event_fields),
        "task_count": data.get("task_count"),
        "task_count_changed": bool(data.get("task_count_changed")),
        "host_plan_window_effect": _compact_fields(
            data.get("host_plan_window_effect"), window_fields
        ),
        "backlog_receipt": _compact_fields(data.get("backlog_receipt"), backlog_fields),
        "model_context_boundary": {
            "schema": _MODEL_CONTEXT_BOUNDARY_SCHEMA,
            "full_backlog_returned": False,
            "full_plan_returned": False,
            "raw_delta_text_returned": False,
            "raw_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
            "write_receipt_only": True,
            "detail_routes": ["pv_status", "pv_task_backlog"],
        },
    }


def _compact_task_activity_result(data: dict[str, Any]) -> dict[str, Any]:
    """Return only the append receipt for one visible ChatLineage activity."""

    event_fields = (
        "event_id",
        "event_type",
        "event_sha256",
        "occurred_at",
        "session_id",
        "task_id",
        "run_id",
        "idempotent_reuse",
    )
    observed_fields = (
        "schema",
        "status",
        "packet_id",
        "packet_sha256",
        "project_id",
        "evidence_session_id",
        "runtime_task_id",
        "plan_task_id",
    )

    def compact_rehydration(payload: object) -> dict[str, Any]:
        """Expose one bounded host window, never the complete Plan authority."""

        if not isinstance(payload, dict):
            return {}
        wrapper = cast(dict[str, Any], payload)
        receipt_value = wrapper.get("receipt")
        receipt = (
            cast(dict[str, Any], receipt_value)
            if isinstance(receipt_value, dict)
            else wrapper
        )
        projection_value = receipt.get("projection")
        projection = (
            cast(dict[str, Any], projection_value)
            if isinstance(projection_value, dict)
            else {}
        )
        contract_value = projection.get("host_update_plan_contract")
        contract = (
            cast(dict[str, Any], contract_value)
            if isinstance(contract_value, dict)
            else {}
        )
        raw_items = contract.get("plan")
        plan_items = [
            {
                "status": item.get("status"),
                "step": item.get("step"),
            }
            for item in (
                cast(list[Any], raw_items) if isinstance(raw_items, list) else []
            )[:10]
            if isinstance(item, dict)
        ]
        header_value = projection.get("continuity_header")
        header = (
            cast(dict[str, Any], header_value) if isinstance(header_value, dict) else {}
        )
        return {
            "state": wrapper.get("state"),
            "request_sha256": wrapper.get("request_sha256"),
            "schema": receipt.get("schema"),
            "status": receipt.get("status"),
            "project_id": receipt.get("project_id"),
            "evidence_session_id": receipt.get("evidence_session_id"),
            "host_task_id_sha256": receipt.get("host_task_id_sha256"),
            "trigger": receipt.get("trigger"),
            "trigger_event_id": receipt.get("trigger_event_id"),
            "action": receipt.get("action"),
            "host_update_plan_required": receipt.get("host_update_plan_required"),
            "host_goal_active": receipt.get("host_goal_active"),
            "host_artifact_visibility_status": receipt.get(
                "host_artifact_visibility_status"
            ),
            "receipt_sha256": receipt.get("receipt_sha256"),
            "projection": {
                "schema": projection.get("schema"),
                "projection_sha256": projection.get("projection_sha256"),
                "canonical_plan_sha256": projection.get("canonical_plan_sha256"),
                "executable_projection_sha256": projection.get(
                    "executable_projection_sha256"
                ),
                "window_ui_fingerprint_sha256": projection.get(
                    "window_ui_fingerprint_sha256"
                ),
                "row_start": projection.get("row_start"),
                "row_end": projection.get("row_end"),
                "item_count": projection.get("item_count"),
                "sole_active_row": projection.get("sole_active_row"),
                "next_hil_boundary_row": header.get("next_hil_boundary_row"),
                "physically_final_row": header.get("physically_final_row"),
                "host_update_plan_contract": {
                    "explanation": contract.get("explanation"),
                    "plan": plan_items,
                },
            },
            "candidate_created": receipt.get("candidate_created"),
            "pending_hil_mutated": receipt.get("pending_hil_mutated"),
            "pointer_moved": receipt.get("pointer_moved"),
            "plan_lane_mutated": receipt.get("plan_lane_mutated"),
            "full_plan_returned": False,
            "bounded_host_window_returned": bool(plan_items),
        }

    event = _compact_fields(data.get("event"), event_fields)
    return {
        "schema": "evidence-lane.task-activity-write-receipt.v1",
        "status": str(data.get("status") or "PASS"),
        "write_committed": str(data.get("status") or "PASS") == "PASS",
        "event": event,
        "activity_type": (
            str(event.get("event_type") or "").removeprefix("task.") if event else None
        ),
        "observed_experience": _compact_fields(
            data.get("observed_experience"), observed_fields
        ),
        "host_plan_rehydration": compact_rehydration(data.get("host_plan_rehydration")),
        "source_state": data.get("source_state"),
        "accepted_pv_query_scope": data.get("accepted_pv_query_scope"),
        "model_context_boundary": {
            "schema": _MODEL_CONTEXT_BOUNDARY_SCHEMA,
            "write_receipt_only": True,
            "visible_payload_returned": False,
            "full_chatlineage_returned": False,
            "full_backlog_returned": False,
            "full_plan_returned": False,
            "raw_markdown_returned": False,
            "raw_file_returned": False,
            "raw_sqlite_returned": False,
            "raw_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
            "detail_routes": ["task_record_activity", "pv_query"],
        },
    }


def _bound_public_result(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    """Enforce one byte-bounded public result across every invocation route."""

    if tool == "pv_plan_tasks":
        bounded = _compact_plan_tasks_result(data)
    elif tool == "pv_plan_steer_delta":
        bounded = _compact_plan_steer_result(data)
    elif tool == "task_record_activity":
        bounded = _compact_task_activity_result(data)
    elif tool in {"fetch", "lane_fetch"}:
        bounded = _compact_fetch_result(data)
    else:
        bounded = data

    forbidden_paths = _authority_field_paths(bounded)
    if forbidden_paths:
        encoded = canonical_json_bytes(bounded)
        return {
            "schema": _MODEL_CONTEXT_WITHHELD_SCHEMA,
            "status": str(bounded.get("status") or "PASS"),
            "tool": tool,
            "payload_withheld": True,
            "reason": "FULL_AUTHORITY_FIELD_BLOCKED",
            "forbidden_field_paths": forbidden_paths,
            "result_bytes": len(encoded),
            "result_sha256": sha256_bytes(encoded),
            "max_public_data_bytes": _MODEL_CONTEXT_DATA_MAX_BYTES,
            "max_public_result_bytes": _MODEL_CONTEXT_RESULT_MAX_BYTES,
            "max_public_approx_tokens": _MODEL_CONTEXT_APPROX_TOKEN_MAX,
            "full_authority_returned": False,
            "raw_payload_returned": False,
            "detail_routes": list(_MODEL_CONTEXT_DETAIL_ROUTES),
            "next_action": "Use one exact bounded query or smaller line/row window.",
        }

    bounded = cast(
        dict[str, Any],
        _bound_nested_public_value(bounded),
    )
    encoded = canonical_json_bytes(bounded)
    result_sha256 = sha256_bytes(encoded)
    if len(encoded) > _MODEL_CONTEXT_DATA_MAX_BYTES:
        safe_scalars = {
            key: value
            for key, value in bounded.items()
            if key
            in {
                "status",
                "state",
                "schema",
                "project_id",
                "session_id",
                "task_id",
                "candidate_id",
                "event_id",
                "pending_hil",
                "pointer_moved",
                "mutation_performed",
            }
            and (value is None or isinstance(value, (str, int, float, bool)))
        }
        return {
            "schema": _MODEL_CONTEXT_WITHHELD_SCHEMA,
            "status": str(bounded.get("status") or "PASS"),
            "tool": tool,
            **safe_scalars,
            "payload_withheld": True,
            "result_bytes": len(encoded),
            "result_sha256": result_sha256,
            "max_public_data_bytes": _MODEL_CONTEXT_DATA_MAX_BYTES,
            "max_public_result_bytes": _MODEL_CONTEXT_RESULT_MAX_BYTES,
            "max_public_approx_tokens": _MODEL_CONTEXT_APPROX_TOKEN_MAX,
            "approx_tokens": (len(encoded) + 3) // 4,
            "top_level_keys": sorted(str(key) for key in bounded)[:64],
            "full_authority_returned": False,
            "raw_payload_returned": False,
            "detail_routes": list(_MODEL_CONTEXT_DETAIL_ROUTES),
            "next_action": (
                "Use one exact bounded query or a smaller line/row window."
            ),
        }

    # Route-specific ``model_context_boundary`` objects are public contracts in
    # their own right.  Keep them byte-stable and add the generic byte receipt
    # beside them rather than silently widening their schema.
    bounded["public_result_boundary"] = {
        "schema": _MODEL_CONTEXT_BOUNDARY_SCHEMA,
        "payload_withheld": False,
        "result_bytes": len(encoded),
        "result_sha256": result_sha256,
        "max_public_data_bytes": _MODEL_CONTEXT_DATA_MAX_BYTES,
        "max_public_result_bytes": _MODEL_CONTEXT_RESULT_MAX_BYTES,
        "max_public_approx_tokens": _MODEL_CONTEXT_APPROX_TOKEN_MAX,
        "approx_tokens": (len(encoded) + 3) // 4,
        "bounded_public_result": True,
    }
    return bounded


def _bound_public_warnings(payload: object) -> list[Any]:
    """Keep warnings list-shaped while withholding oversized diagnostic prose."""

    redacted = redact(payload)
    warnings = redacted if isinstance(redacted, list) else [redacted]
    encoded = canonical_json_bytes(warnings)
    if len(encoded) <= _MODEL_CONTEXT_AUXILIARY_MAX_BYTES:
        return cast(list[Any], warnings)
    return [
        {
            "schema": _MODEL_CONTEXT_AUXILIARY_WITHHELD_SCHEMA,
            "kind": "warnings",
            "payload_withheld": True,
            "result_bytes": len(encoded),
            "result_sha256": sha256_bytes(encoded),
            "max_public_auxiliary_bytes": _MODEL_CONTEXT_AUXILIARY_MAX_BYTES,
            "next_action": "Use the exact receipt or bounded diagnostic query.",
        }
    ]


def _bound_public_error(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve the error identity but never return unbounded error details."""

    redacted = cast(dict[str, Any], redact(payload))
    encoded = canonical_json_bytes(redacted)
    if len(encoded) <= _MODEL_CONTEXT_AUXILIARY_MAX_BYTES:
        return redacted
    details = redacted.get("details")
    detail_keys = (
        sorted(str(key) for key in details)[:64] if isinstance(details, dict) else []
    )
    message = str(redacted.get("message") or "Governed operation failed.")
    return {
        "schema": _MODEL_CONTEXT_AUXILIARY_WITHHELD_SCHEMA,
        "kind": "error",
        "status": str(redacted.get("status") or "FAIL"),
        "code": str(redacted.get("code") or "GOVERNED_OPERATION_FAILED"),
        "message": message[:512],
        "payload_withheld": True,
        "result_bytes": len(encoded),
        "result_sha256": sha256_bytes(encoded),
        "detail_keys": detail_keys,
        "max_public_auxiliary_bytes": _MODEL_CONTEXT_AUXILIARY_MAX_BYTES,
        "next_action": "Use one exact bounded diagnostic query.",
    }


def _bound_public_envelope(tool: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Apply the hard ceiling to the complete tool envelope, not only ``data``."""

    encoded = canonical_json_bytes(envelope)
    if len(encoded) <= _MODEL_CONTEXT_RESULT_MAX_BYTES:
        return envelope

    execution_status = str(
        envelope.get("execution_status") or envelope.get("status") or "PASS"
    )
    domain_status = str(envelope.get("domain_status") or execution_status)
    compact_error = envelope.get("error")
    if isinstance(compact_error, dict):
        compact_error = _compact_fields(
            compact_error,
            (
                "schema",
                "kind",
                "status",
                "code",
                "message",
                "payload_withheld",
                "result_bytes",
                "result_sha256",
            ),
        )
    minimal: dict[str, Any] = {
        "schema": envelope.get("schema"),
        "tool": tool,
        "status": execution_status,
        "execution_status": execution_status,
        "domain_status": domain_status,
        "data": (
            {
                "schema": _MODEL_CONTEXT_WITHHELD_SCHEMA,
                "status": domain_status,
                "tool": tool,
                "payload_withheld": True,
                "full_envelope_withheld": True,
                "result_bytes": len(encoded),
                "result_sha256": sha256_bytes(encoded),
                "max_public_result_bytes": _MODEL_CONTEXT_RESULT_MAX_BYTES,
                "full_authority_returned": False,
                "raw_payload_returned": False,
                "detail_routes": list(_MODEL_CONTEXT_DETAIL_ROUTES),
                "next_action": "Use one exact bounded query or smaller line/row window.",
            }
            if envelope.get("data") is not None
            else None
        ),
        "warnings": [],
        "error": compact_error,
        "provenance": envelope.get("provenance"),
        "entry_authorization": envelope.get("entry_authorization"),
    }
    # The minimal form contains only fixed-size scalars and hashes.  Keep this
    # assertion adjacent to the boundary so future envelope drift fails tests.
    assert len(canonical_json_bytes(minimal)) <= _MODEL_CONTEXT_RESULT_MAX_BYTES
    return minimal


def _lane_projection(
    store: ProjectStore,
    project_id: str,
    accepted_pv: str | None,
) -> dict[str, Any]:
    """Return compact public-safe lane facts from the live authority.

    External project authority always reads ``sectors/``.  Its accepted PV is
    pointer provenance only; ordinary status/query routes never open the
    immutable accepted archive.  The package projection remains solely for
    legacy in-store projects that have no external live authority.
    """

    if not accepted_pv:
        return {
            "authority": "NO_ACCEPTED_PV",
            "pv_ref": None,
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "emitted_lane_count": 0,
            "absent_lane_ids": list(CANONICAL_LANE_IDS),
            "lanes": [
                {
                    "id": lane_id,
                    "label": LANE_REGISTRY[lane_id].display_label,
                    "value": "NO ACCEPTED PV | no lane authority available",
                    "state": "NO_ACCEPTED_PV",
                    "contract_status": "NOT_APPLICABLE",
                    "member_count": 0,
                }
                for lane_id in CANONICAL_LANE_IDS
            ],
        }

    if store.uses_external_project_authority(project_id):
        working_root = store.project_root(project_id) / "sectors"
        manifest_path = working_root / "manifest.json"
        if not manifest_path.is_file():
            return {
                "authority": "ACCEPTED_POINTER_REFERENCE_ONLY",
                "pv_ref": accepted_pv,
                "accepted_artifact_available": None,
                "accepted_archive_queried": False,
                "canonical_lane_count": len(CANONICAL_LANE_IDS),
                "emitted_lane_count": 0,
                "absent_lane_ids": list(CANONICAL_LANE_IDS),
                "lanes": [],
                "topology_status": "UNAVAILABLE",
            }
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        emitted = {
            str(lane_id)
            for lane_id in (manifest.get("emitted_lane_ids") or [])
            if str(lane_id) in CANONICAL_LANE_IDS
        }
        working_lanes: list[dict[str, Any]] = []
        for lane_id in CANONICAL_LANE_IDS:
            lane_manifest_path = working_root / lane_id / "lane_manifest.json"
            lane_manifest = (
                json.loads(lane_manifest_path.read_text(encoding="utf-8"))
                if lane_manifest_path.is_file()
                else {}
            )
            state = "EMITTED" if lane_id in emitted else "NOT_EMITTED"
            working_lanes.append(
                {
                    "id": lane_id,
                    "label": LANE_REGISTRY[lane_id].display_label,
                    "value": (
                        f"{state} | WORKING | "
                        f"{int(lane_manifest.get('source_count') or 0)} sources | "
                        f"accepted pointer {accepted_pv}"
                    ),
                    "state": state,
                    "contract_status": str(
                        lane_manifest.get("status") or "WORKING_AUTHORITY"
                    ),
                    "member_count": 4 if lane_manifest else 0,
                    "authority": "WORKING_SECTORS",
                    "pv_ref": None,
                }
            )
        return {
            "authority": "WORKING_SECTORS_WITH_ACCEPTED_POINTER_REFERENCE",
            "pv_ref": accepted_pv,
            "accepted_artifact_available": None,
            "accepted_archive_queried": False,
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "manifest_declared_canonical_lane_count": int(
                manifest.get("canonical_lane_count") or 0
            ),
            "emitted_lane_count": len(emitted),
            "absent_lane_ids": [
                lane_id for lane_id in CANONICAL_LANE_IDS if lane_id not in emitted
            ],
            "bundle_sha256": manifest.get("bundle_sha256"),
            "topology_status": (
                "PASS" if len(emitted) == len(CANONICAL_LANE_IDS) else "NOT_PROVEN"
            ),
            "lanes": working_lanes,
        }

    manifest = store.accepted_manifest(project_id, accepted_pv)
    universal = manifest.get("universal_lanes")
    if not isinstance(universal, dict):
        universal = {}
    emitted = {
        str(lane_id)
        for lane_id in (universal.get("emitted_lane_ids") or [])
        if str(lane_id) in CANONICAL_LANE_IDS
    }
    contracts = universal.get("four_file_contracts")
    if not isinstance(contracts, dict):
        contracts = {}
    lanes: list[dict[str, Any]] = []
    for lane_id in CANONICAL_LANE_IDS:
        contract = contracts.get(lane_id)
        if not isinstance(contract, dict):
            contract = {}
        members = contract.get("members")
        member_count = len(members) if isinstance(members, list) else 0
        state = "EMITTED" if lane_id in emitted else "NOT_EMITTED"
        contract_status = (
            str(contract.get("status") or "UNKNOWN")
            if lane_id in emitted
            else "NOT_APPLICABLE"
        )
        lanes.append(
            {
                "id": lane_id,
                "label": LANE_REGISTRY[lane_id].display_label,
                "value": (
                    f"{state} | {contract_status} | {member_count} sealed files | "
                    f"accepted {accepted_pv}"
                ),
                "state": state,
                "contract_status": contract_status,
                "member_count": member_count,
                "authority": "ACCEPTED_IMMUTABLE_AUTHORITY",
                "pv_ref": accepted_pv,
            }
        )
    absent = [lane_id for lane_id in CANONICAL_LANE_IDS if lane_id not in emitted]
    return {
        "authority": "ACCEPTED_IMMUTABLE_AUTHORITY",
        "pv_ref": accepted_pv,
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "manifest_declared_canonical_lane_count": int(
            universal.get("canonical_lane_count") or 0
        ),
        "emitted_lane_count": len(emitted),
        "absent_lane_ids": absent,
        "bundle_sha256": universal.get("bundle_sha256"),
        "topology_status": (
            "PASS" if universal.get("topology_valid") is True else "NOT_PROVEN"
        ),
        "lanes": lanes,
    }


class EvidenceLaneService:
    def __init__(
        self,
        *,
        data_root: str | Path | None = None,
        sync_service: PVSyncService | None = None,
    ) -> None:
        repository_root = identity_repository_root(__file__)
        configured_runtime_root = os.environ.get(
            "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"
        )
        hidden_runtime_root = (
            Path.home() / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
        ).resolve()
        if data_root is not None:
            require(
                bool(os.fspath(data_root).strip()),
                "EVIDENCE_LANE_DATA_ROOT_INVALID",
                "An explicitly configured Evidence Lane data root cannot be empty.",
                status="BLOCKED",
            )
            configured_root = Path(data_root)
            root_source = "EXPLICIT_SERVICE_CONFIGURATION"
        elif configured_runtime_root is not None:
            require(
                bool(configured_runtime_root.strip()),
                "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT_INVALID",
                "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT cannot be empty when configured.",
                status="BLOCKED",
            )
            configured_root = Path(configured_runtime_root)
            root_source = "HIDDEN_PLUGIN_RUNTIME_CONTROL_ROOT"
        else:
            configured_root = hidden_runtime_root
            root_source = "HIDDEN_PLUGIN_RUNTIME_CONTROL_DEFAULT"
        self.store = ProjectStore(
            configured_root,
            configuration_source=root_source,
        )
        self.flash_authority = SessionFlashAuthority(data_root=configured_root)
        self.runtime_activation = RuntimeActivation(configured_root)
        self.storage_selection = StorageSelection(self.store)
        self.engine = CodePVEngine(
            store=self.store,
            source_repository_root=repository_root,
            package_source_root=Path(__file__).resolve().parent,
        )
        self.sessions = SessionManager(
            self.store,
            self.engine,
            runtime_activation=self.runtime_activation,
        )
        self.reader = PVReader(self.store)
        self.lane_reader = LaneReader(self.store)
        self.remote_git = RemoteGitController(self.store)
        self.sync_service = sync_service or self._environment_sync_service()
        self._agent_configuration_manager = AgentConfigurationManager()
        self._conversation_memory_manager = ConversationMemoryManager()

    def agent_configuration_authority(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        working_directory: str | Path | None = None,
    ) -> dict[str, Any]:
        """Return the bounded AGENTS.md authority for one exact active task."""

        exact_session_id = str(session_id or "").strip()
        if not exact_session_id:
            active_path = self.store.project_root(project_id) / "active_session.json"
            require(
                active_path.is_file(),
                "AGENT_CONFIGURATION_ACTIVE_SESSION_REQUIRED",
                "AGENTS.md authority requires the exact governed session.",
                status="MISMATCH",
            )
            exact_session_id = str(
                json.loads(active_path.read_text(encoding="utf-8")).get("session_id")
                or ""
            ).strip()
        session = self.sessions.load(project_id, exact_session_id)
        require(
            session.project_id == project_id
            and not bool(session.metadata.get("closed_at")),
            "AGENT_CONFIGURATION_SESSION_BINDING_MISMATCH",
            "AGENTS.md authority cannot cross a project or closed-session boundary.",
            status="MISMATCH",
        )
        config = self.store.config(project_id)
        project_root = Path(config.repository_path).resolve()
        exact_cwd = project_root
        workspace_path = Path(str(session.workspace_id or ""))
        if working_directory is not None:
            exact_cwd = Path(working_directory).resolve()
        elif workspace_path.is_absolute() and workspace_path.exists():
            exact_cwd = workspace_path.resolve()
        host_task_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        active_plan_task_id = str(
            session.metadata.get("active_backlog_task_id")
            or (session.task or {}).get("backlog_task_id")
            or (session.task or {}).get("task_id")
            or "NO_ACTIVE_PLAN_ROW"
        ).strip()
        profile_value = session.metadata.get("execution_profile")
        profile = dict(profile_value) if isinstance(profile_value, dict) else {}
        codex_home = Path(
            str(os.environ.get("CODEX_HOME") or "").strip() or (Path.home() / ".codex")
        )
        resolved = self._agent_configuration_manager.resolve(
            codex_home=codex_home,
            project_root=project_root,
            cwd=exact_cwd,
            project_id=project_id,
            governed_session_id=exact_session_id,
            host_task_id=host_task_id,
            host_task_deep_link=f"codex://threads/{host_task_id}",
            host_session_id=host_task_id,
            workspace_id=session.workspace_id,
            active_plan_task_id=active_plan_task_id,
            execution_profile=profile,
        )
        return dict(resolved.receipt)

    def _active_agent_configuration_authority(
        self, project_id: str
    ) -> dict[str, Any] | None:
        active_path = self.store.project_root(project_id) / "active_session.json"
        if not active_path.is_file():
            return None
        exact_session_id = str(
            json.loads(active_path.read_text(encoding="utf-8")).get("session_id") or ""
        ).strip()
        if not exact_session_id:
            return None
        session = self.sessions.load(project_id, exact_session_id)
        if session.metadata.get("closed_at"):
            return None
        try:
            return self.agent_configuration_authority(
                project_id,
                session_id=exact_session_id,
            )
        except EvidenceLaneError as exc:
            if exc.code != "AGENT_CONFIGURATION_BINDING_INVALID":
                raise
            return {
                "schema": "evidence-lane.agent-configuration-authority.v1",
                "status": "NOT_BOUND",
                "reason": "EXACT_RUNTIME_BINDING_UNAVAILABLE",
                "missing_field": exc.details.get("field"),
                "authority_effects": {
                    "instruction_context_applied": False,
                    "permission_widening_allowed": False,
                    "hil_inference_allowed": False,
                    "pointer_movement_allowed": False,
                },
            }

    def conversation_memory_authority(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        working_directory: str | Path | None = None,
    ) -> dict[str, Any]:
        """Return bounded task-bound ``MEMORY.md`` continuation authority."""

        exact_session_id = str(session_id or "").strip()
        if not exact_session_id:
            active_path = self.store.project_root(project_id) / "active_session.json"
            require(
                active_path.is_file(),
                "CONVERSATION_MEMORY_ACTIVE_SESSION_REQUIRED",
                "MEMORY.md authority requires the exact governed session.",
                status="MISMATCH",
            )
            exact_session_id = str(
                json.loads(active_path.read_text(encoding="utf-8")).get("session_id")
                or ""
            ).strip()
        session = self.sessions.load(project_id, exact_session_id)
        require(
            session.project_id == project_id
            and not bool(session.metadata.get("closed_at")),
            "CONVERSATION_MEMORY_SESSION_BINDING_MISMATCH",
            "MEMORY.md authority cannot cross a project or closed-session boundary.",
            status="MISMATCH",
        )
        config = self.store.config(project_id)
        project_root = Path(config.repository_path).resolve()
        workspace_path = Path(str(session.workspace_id or ""))
        exact_cwd = project_root
        if working_directory is not None:
            exact_cwd = Path(working_directory).resolve()
        elif workspace_path.is_absolute() and workspace_path.exists():
            exact_cwd = workspace_path.resolve()
        host_task_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        active_plan_task_id = str(
            session.metadata.get("active_backlog_task_id")
            or (session.task or {}).get("backlog_task_id")
            or (session.task or {}).get("task_id")
            or "NO_ACTIVE_PLAN_ROW"
        ).strip()
        profile_value = session.metadata.get("execution_profile")
        profile = dict(profile_value) if isinstance(profile_value, dict) else {}
        codex_home = Path(
            str(os.environ.get("CODEX_HOME") or "").strip() or (Path.home() / ".codex")
        )
        resolved = self._conversation_memory_manager.resolve(
            codex_home=codex_home,
            project_root=project_root,
            cwd=exact_cwd,
            project_id=project_id,
            governed_session_id=exact_session_id,
            host_task_id=host_task_id,
            host_task_deep_link=f"codex://threads/{host_task_id}",
            host_session_id=host_task_id,
            workspace_id=session.workspace_id,
            active_plan_task_id=active_plan_task_id,
            execution_profile=profile,
        )
        return dict(resolved.receipt)

    def _active_conversation_memory_authority(
        self, project_id: str
    ) -> dict[str, Any] | None:
        active_path = self.store.project_root(project_id) / "active_session.json"
        if not active_path.is_file():
            return None
        exact_session_id = str(
            json.loads(active_path.read_text(encoding="utf-8")).get("session_id") or ""
        ).strip()
        if not exact_session_id:
            return None
        session = self.sessions.load(project_id, exact_session_id)
        if session.metadata.get("closed_at"):
            return None
        try:
            return self.conversation_memory_authority(
                project_id,
                session_id=exact_session_id,
            )
        except EvidenceLaneError as exc:
            if exc.code != "CONVERSATION_MEMORY_BINDING_INVALID":
                raise
            return {
                "schema": "evidence-lane.conversation-memory-authority.v1",
                "status": "NOT_BOUND",
                "reason": "EXACT_RUNTIME_BINDING_UNAVAILABLE",
                "missing_field": exc.details.get("field"),
                "authority_effects": {
                    "bounded_continuation_guidance_applied": False,
                    "host_compaction_disabled": False,
                    "permission_widening_allowed": False,
                    "hil_inference_allowed": False,
                    "pointer_movement_allowed": False,
                },
            }

    def _public_entry_binding_receipt(
        self,
        *,
        tool_name: str,
        lifecycle: bool,
        project_id: str | None,
        session_id: str | None,
        invocation_arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Attest one public action before its callback observes authority."""

        exact_tool = str(tool_name or "").strip()
        exact_project = str(project_id or "").strip()
        requested_session = str(session_id or "").strip()
        require(
            bool(exact_tool),
            "PUBLIC_ENTRY_TOOL_REQUIRED",
            "The common public entry boundary requires one exact action name.",
            status="BLOCKED",
        )
        flash = self.flash_authority.status()
        env_uop: dict[str, str] | None = None
        if exact_project or lifecycle:
            env_uop = derive_host_entry_env_uop(flash)
        else:
            try:
                env_uop = derive_host_entry_env_uop(flash)
            except EvidenceLaneError:
                # Global diagnostic/catalog reads must truthfully report an
                # inactive Flash instead of becoming impossible to invoke.
                env_uop = None
        runtime_attestation = dict(self.sessions._runtime_instance_attestation)
        require(
            runtime_attestation.get("status") == "PASS"
            and runtime_attestation.get("caller_supplied") is False,
            "PUBLIC_ENTRY_RUNTIME_ATTESTATION_INVALID",
            "The common public entry boundary requires server-derived runtime identity.",
            status="MISMATCH",
        )
        binding: dict[str, Any] = {
            "binding_mode": "SERVER_RUNTIME_GLOBAL",
            "project_id": None,
            "governed_session_id": None,
            "active_task_id": None,
            "host_session_id_sha256": None,
            "execution_profile_sha256": None,
            "agent_configuration_authority_sha256": None,
            "conversation_memory_authority_sha256": None,
        }
        if exact_project:
            allow_unregistered = exact_tool in {"project_enroll", "project_register"}
            if not allow_unregistered:
                self.store.config(exact_project)
            binding.update(
                {
                    "binding_mode": (
                        "PROJECT_REGISTRATION_ENTRY"
                        if allow_unregistered
                        else "PROJECT_ROUTE_ENTRY"
                    ),
                    "project_id": exact_project,
                }
            )
            active_path = self.store.project_root(exact_project) / "active_session.json"
            active_session_id = ""
            if active_path.is_file():
                active_session_id = str(
                    json.loads(active_path.read_text(encoding="utf-8")).get(
                        "session_id"
                    )
                    or ""
                ).strip()
            require(
                not requested_session
                or not active_session_id
                or requested_session == active_session_id,
                "PUBLIC_ENTRY_SESSION_BINDING_MISMATCH",
                "The requested governed session differs from the active project session.",
                status="MISMATCH",
                requested_session_id=requested_session or None,
                active_session_id=active_session_id or None,
            )
            exact_session = requested_session or active_session_id
            if exact_session:
                session = self.sessions.load(exact_project, exact_session)
                active_task_id = str(
                    session.metadata.get("active_backlog_task_id") or ""
                ).strip()
                active_rows = [
                    row
                    for row in self.store.backlog_status(exact_project)[
                        "goal_projection"
                    ]["rows"]
                    if row.get("status") == "in_progress"
                ]
                exact_active_task_bound = (
                    len(active_rows) == 1
                    and bool(active_task_id)
                    and active_rows[0].get("task_id") == active_task_id
                )
                arguments = dict(invocation_arguments or {})
                preapproval_correction_entry = False
                if (
                    not exact_active_task_bound
                    and exact_tool == "pv_task_transition"
                    and arguments.get("transition_name") == "CORRECT_PREAPPROVAL_DONE"
                ):
                    requested_task_id = str(arguments.get("task_id") or "").strip()
                    correction_of_event_id = str(
                        arguments.get("correction_of_event_id") or ""
                    ).strip()
                    expected_backlog_sha256 = (
                        str(arguments.get("expected_backlog_sha256") or "")
                        .strip()
                        .upper()
                    )
                    backlog = self.store._load_backlog(exact_project)
                    task = next(
                        (
                            row
                            for row in backlog.get("tasks", [])
                            if row.get("task_id") == requested_task_id
                        ),
                        None,
                    )
                    correction_event = next(
                        (
                            row
                            for row in backlog.get("events", [])
                            if row.get("event_id") == correction_of_event_id
                        ),
                        None,
                    )
                    preapproval_correction_entry = bool(
                        not active_rows
                        and active_task_id
                        and requested_task_id == active_task_id
                        and isinstance(task, dict)
                        and task.get("status") == "DONE"
                        and task.get("last_event_id") == correction_of_event_id
                        and isinstance(correction_event, dict)
                        and correction_event.get("task_id") == requested_task_id
                        and correction_event.get("event_type") == "TASK_DONE"
                        and correction_event.get("from_status") == "ACTIVE"
                        and correction_event.get("to_status") == "DONE"
                        and expected_backlog_sha256
                        == sha256_bytes(canonical_json_bytes(backlog))
                    )
                require(
                    exact_active_task_bound or preapproval_correction_entry,
                    "PUBLIC_ENTRY_ACTIVE_TASK_BINDING_MISMATCH",
                    "The common entry boundary requires the exact sole active Plan task.",
                    status="MISMATCH",
                    active_task_id=active_task_id or None,
                    active_row_task_ids=[row.get("task_id") for row in active_rows],
                )
                host_session_id = str(
                    session.metadata.get("current_host_session_id") or ""
                ).strip()
                profile = session.metadata.get("execution_profile")
                require(
                    bool(host_session_id) and isinstance(profile, dict),
                    "PUBLIC_ENTRY_HOST_PROFILE_BINDING_REQUIRED",
                    "The common entry boundary requires host-session and execution-profile authority.",
                    status="MISMATCH",
                )
                profile = cast(dict[str, Any], profile)
                agent_configuration = self.agent_configuration_authority(
                    exact_project,
                    session_id=exact_session,
                )
                conversation_memory = self.conversation_memory_authority(
                    exact_project,
                    session_id=exact_session,
                )
                binding.update(
                    {
                        "binding_mode": (
                            "EXACT_PREAPPROVAL_DONE_CORRECTION_ENTRY"
                            if preapproval_correction_entry
                            else "EXACT_ACTIVE_TASK_ENTRY"
                        ),
                        "governed_session_id": exact_session,
                        "active_task_id": active_task_id,
                        "host_session_id_sha256": sha256_bytes(
                            host_session_id.encode("utf-8")
                        ),
                        "execution_profile_sha256": sha256_bytes(
                            canonical_json_bytes(profile)
                        ),
                        "agent_configuration_authority_sha256": (
                            agent_configuration["agent_configuration_authority_sha256"]
                        ),
                        "conversation_memory_authority_sha256": (
                            conversation_memory["conversation_memory_authority_sha256"]
                        ),
                    }
                )
        body = {
            "schema": "evidence-lane.public-entry-binding.v1",
            "status": "PASS",
            "tool_name": exact_tool,
            "effect_class": "WRITE" if lifecycle else "READ",
            "binding": binding,
            "env_uop_entry_status": (
                "ATTESTED" if env_uop is not None else "DIAGNOSTICALLY_UNAVAILABLE"
            ),
            "env_authority_sha256": (
                env_uop["env_authority_sha256"] if env_uop is not None else None
            ),
            "uop_authority_sha256": (
                env_uop["uop_authority_sha256"] if env_uop is not None else None
            ),
            "derived_projection_sha256": (
                env_uop["derived_projection_sha256"] if env_uop is not None else None
            ),
            "flash_receipt_sha256": (
                env_uop["flash_receipt_sha256"] if env_uop is not None else None
            ),
            "runtime_instance_attestation_receipt_sha256": runtime_attestation[
                "receipt_sha256"
            ],
            "caller_supplied_runtime_identity": False,
            "callback_entered": False,
        }
        return {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def _capture_route_binding(self, project_id: str) -> dict[str, Any]:
        """Bind the capture policy before any session lineage ingestion."""

        project_root = self.store.project_root(project_id)
        project_path = project_root / "project.json"
        registered = json.loads(project_path.read_text(encoding="utf-8"))
        config = self.store.config(project_id)
        selection_reason = (
            "EXPLICIT_PROJECT_CONFIGURATION"
            if "capture_route" in registered
            else "LEGACY_PROJECT_COMPATIBILITY_DEFAULT_FULL_ROUTE"
        )
        authority = CaptureRouteAuthority(project_root)
        if authority.binding_path.is_file():
            return {
                "status": "PASS",
                "state": "BOUND_IDEMPOTENT_REUSE",
                **authority.binding(),
            }
        return authority.bind(
            project_id=project_id,
            route=config.capture_route,
            selected_by="ATOMIC_BOOT_OR_RESUME_PRE_INGESTION",
            reason=selection_reason,
        )

    def storage_connector_inspect(
        self,
        project_id: str,
        *,
        host: str | None = None,
        ephemeral: bool = False,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        selection = self.storage_selection.inspect(project_id)
        result: dict[str, Any] = {
            **selection,
            "project_route": self.store.inspect_project_route(project_id),
            "project_authority": self.store.project_authority_status(project_id),
            "configured_runtime_connector_available": bool(
                self.sync_service is not None
                and self.sync_service.runtime_state_capable
            ),
        }
        if host:
            route, _ = self._selected_persistence_route(
                project_id,
                host=host,
                ephemeral=ephemeral,
                server_has_durable_filesystem=server_has_durable_filesystem,
            )
            result["effective_route"] = route.as_dict()
            result["effective_route"]["project_route"] = (
                self.store.inspect_project_route(project_id)
            )
        return result

    def storage_connector_select(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self.storage_selection.select(project_id, **kwargs)

    def _selected_persistence_route(
        self,
        project_id: str,
        *,
        host: str,
        ephemeral: bool,
        server_has_durable_filesystem: bool | None,
        runtime_context: dict[str, Any] | None = None,
        host_session_id: str | None = None,
    ) -> tuple[PersistenceRoute, dict[str, Any]]:
        host_kind = normalize_host_kind(host)
        automatic = route_persistence(
            host_kind,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            host_session_id=host_session_id,
        )
        selection = self.storage_selection.inspect(project_id)
        if selection["mode"] == "AUTO":
            return automatic, selection
        if selection["mode"] == "LOCAL_SQLITE":
            require(
                automatic.server_filesystem == "DURABLE",
                "LOCAL_SQLITE_STORAGE_UNAVAILABLE",
                "The selected local SQLite authority is unavailable on this host.",
                status="BLOCKED",
            )
            return replace(
                automatic,
                mode="local",
                reason="the project explicitly selected its durable local SQLite authority",
                durable_required=False,
            ), selection
        return replace(
            automatic,
            mode="configured_durable_connector",
            reason=(
                "the project explicitly selected the configured transactional "
                f"connector {selection['connector_id']}"
            ),
            durable_required=True,
            host_connector_role="PRIMARY_TRANSACTIONAL_RUNTIME_AUTHORITY",
            primary_runtime_authority="CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
        ), selection

    def _persistence_route_payload(
        self,
        project_id: str,
        route: PersistenceRoute,
    ) -> dict[str, Any]:
        return {
            **route.as_dict(),
            "project_route": self.store.inspect_project_route(project_id),
            "transport_project_binding": "EXPLICIT_PROJECT_ID_PER_PROJECT_SCOPED_TOOL",
            "cross_project_fallback_allowed": False,
        }

    def _consume_remote_host_entry_for_resume(
        self,
        project_id: str,
        *,
        route: PersistenceRoute,
        host_session_id: str,
        runtime_context: dict[str, Any] | None,
        project_lineage_entry: dict[str, Any],
        flash: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Gate insufficiently durable resume on one exact host-entry claim."""

        if route.server_filesystem == "DURABLE":
            return None
        require(
            route.durable_required
            and self.sync_service is not None
            and self.sync_service.runtime_state_capable,
            "DURABLE_RUNTIME_CONNECTOR_NOT_CONFIGURED",
            "Remote host entry requires the configured transactional runtime connector.",
            status="BLOCKED",
        )
        context = cast(dict[str, Any], runtime_context or {})
        envelope = context.get("host_entry_envelope")
        invocation_id = str(context.get("host_entry_invocation_id") or "").strip()
        require(
            isinstance(envelope, dict) and bool(invocation_id),
            "HOST_ENTRY_ENVELOPE_REQUIRED_BEFORE_RESUME",
            "An insufficiently durable host must provide one exact host-entry envelope and invocation identity before Resume.",
            status="BLOCKED",
        )
        envelope = cast(dict[str, Any], envelope)
        active_path = self.store.project_root(project_id) / "active_session.json"
        require(
            active_path.is_file(),
            "HOST_ENTRY_ACTIVE_SESSION_REQUIRED",
            "Remote host entry requires the exact persistent governed session.",
            status="BLOCKED",
        )
        active = json.loads(active_path.read_text(encoding="utf-8"))
        session = self.sessions.load(project_id, str(active["session_id"]))
        projection = self.store.backlog_status(project_id)["goal_projection"]
        active_rows = [
            row for row in projection["rows"] if row["status"] == "in_progress"
        ]
        require(
            len(active_rows) == 1,
            "HOST_ENTRY_ACTIVE_PLAN_ROW_REQUIRED",
            "Remote host entry requires one and only one active canonical Plan row.",
            status="MISMATCH",
            active_count=len(active_rows),
        )
        active_row = active_rows[0]
        pointer = self.store.pointer(project_id)
        pointer_payload = pointer.as_dict()
        expected_pointer = {
            "accepted_pv": pointer.accepted_pv,
            "generation": pointer.generation,
            "manifest_sha256": pointer.accepted_manifest_sha256,
            "pointer_sha256": sha256_bytes(canonical_json_bytes(pointer_payload)),
        }
        lineage_head = str(project_lineage_entry.get("head_state_sha256") or "")
        authority_heads = derive_host_entry_authority_heads(
            self.store.project_root(project_id),
            project_id=project_id,
            accepted_pointer=pointer_payload,
            chat_lineage_head_sha256=lineage_head,
        )
        env_uop = derive_host_entry_env_uop(flash)
        config = self.store.config(project_id)
        worktree_sha256 = calculate_worktree_sha256(config.repository_path)
        exact_host_session_id = host_session_id.strip()
        consumer_binding = {
            "task_id": exact_host_session_id,
            "task_deep_link_sha256": sha256_bytes(
                f"codex://threads/{exact_host_session_id}".encode()
            ),
            "host_binding_id": envelope.get("host_binding_id"),
            "host_session_id_sha256": sha256_bytes(
                exact_host_session_id.encode("utf-8")
            ),
            "invocation_id_sha256": sha256_bytes(invocation_id.encode("utf-8")),
        }
        candidate_overlay_sha256 = (
            sha256_bytes(
                canonical_json_bytes(
                    {
                        "candidate_id": session.candidate_id,
                        "session_state": session.state.value,
                        "accepted_pointer_generation": pointer.generation,
                    }
                )
            )
            if session.candidate_id
            else None
        )
        return consume_host_entry_envelope(
            self.store.project_root(project_id),
            cast(dict[str, Any], envelope),
            expected_project_id=project_id,
            expected_evidence_session_id=session.session_id,
            expected_plan_task_id=str(active_row["task_id"]),
            expected_pointer=expected_pointer,
            expected_active_plan_row=int(active_row["number"]),
            expected_env_uop=env_uop,
            expected_worktree_sha256=worktree_sha256,
            expected_authority_heads=authority_heads,
            consumer_binding=consumer_binding,
            consumed_at=utc_now(),
            backend=cast(
                TransactionalHostEntryBackend,
                cast(PVSyncService, self.sync_service).backend,
            ),
            expected_candidate_overlay_sha256=candidate_overlay_sha256,
        )

    def _environment_sync_service(self) -> PVSyncService | None:
        token = os.environ.get("EVIDENCE_LANE_GOOGLE_DRIVE_ACCESS_TOKEN", "")
        folder = os.environ.get("EVIDENCE_LANE_GOOGLE_DRIVE_FOLDER_ID", "")
        if not token and not folder:
            return None
        backend = GoogleDrivePersistence(
            access_token=token,
            parent_folder_id=folder,
        )
        return PVSyncService(
            store=self.store,
            backend=backend,
            drive_encryption_key=os.environ.get("EVIDENCE_LANE_DRIVE_ENCRYPTION_KEY"),
        )

    @staticmethod
    def _result(
        tool: str,
        data: dict[str, Any],
        *,
        lifecycle: bool = False,
        entry_authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        domain_status = str(data.get("status", "PASS"))
        execution_status = "PASS"
        # Warnings have their own bounded envelope field.  Excluding them from
        # ``data`` prevents one diagnostic from being returned twice or from
        # withholding an otherwise small successful receipt.
        public_data = {key: value for key, value in data.items() if key != "warnings"}
        redacted = cast(dict[str, Any], redact(public_data))
        bounded = _bound_public_result(tool, redacted)
        envelope = {
            "schema": LIFECYCLE_RESULT_SCHEMA if lifecycle else TOOL_RESULT_SCHEMA,
            "tool": tool,
            "status": execution_status,
            "execution_status": execution_status,
            "domain_status": domain_status,
            "data": bounded,
            "warnings": _bound_public_warnings(data.get("warnings", [])),
            "error": None,
            "provenance": {
                "engine": "evidence-lane-universal-pv-engine",
                "generated_at": utc_now(),
                "fabricated_evidence": False,
            },
        }
        if entry_authorization is not None:
            envelope["entry_authorization"] = entry_authorization
        return _bound_public_envelope(tool, envelope)

    @staticmethod
    def _error(
        tool: str,
        error: Exception,
        *,
        lifecycle: bool = False,
        entry_authorization: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if isinstance(error, EvidenceLaneError):
            payload = error.as_dict()
        else:
            payload = {
                "code": "UNEXPECTED_INTERNAL_ERROR",
                "message": "The operation failed inside the governed plugin boundary.",
                "status": "FAIL",
                "details": {
                    "type": type(error).__name__,
                    "trace_id": __import__("uuid").uuid4().hex,
                },
            }
        envelope = {
            "schema": LIFECYCLE_RESULT_SCHEMA if lifecycle else TOOL_RESULT_SCHEMA,
            "tool": tool,
            "status": "FAIL",
            "execution_status": "FAIL",
            "domain_status": payload["status"],
            "data": None,
            "warnings": [],
            "error": _bound_public_error(payload),
            "provenance": {
                "engine": "evidence-lane-universal-pv-engine",
                "generated_at": utc_now(),
                "fabricated_evidence": False,
            },
        }
        if entry_authorization is not None:
            envelope["entry_authorization"] = entry_authorization
        return _bound_public_envelope(tool, envelope)

    def invoke(
        self,
        tool: str,
        function: Callable[..., dict[str, Any]],
        /,
        *args: Any,
        lifecycle: bool = False,
        entry_authorization: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return self._result(
                tool,
                function(*args, **kwargs),
                lifecycle=lifecycle,
                entry_authorization=entry_authorization,
            )
        # MCP tools must return the canonical fail-closed envelope even when an
        # unexpected library or OS exception crosses the private-engine boundary.
        except Exception as error:  # noqa: BLE001
            return self._error(
                tool,
                error,
                lifecycle=lifecycle,
                entry_authorization=entry_authorization,
            )

    def doctor(self) -> dict[str, Any]:
        installation = self.sessions.installation_status()
        report = self.engine.doctor()
        flash = self.flash_authority.status()
        runtime_activation = self.runtime_activation.status()
        report["installation"] = installation
        report["session_flash"] = flash
        report["runtime_activation"] = runtime_activation
        report["store_routing"] = self.store.inspect_root()
        report["model_compatibility"] = model_compatibility_catalog()
        report["checks"]["session_flash_bundle"] = flash["status"] == "PASS"
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
        report["warnings"] = flash["warnings"]
        report["google_drive_configured"] = bool(
            self.sync_service is not None
            and isinstance(self.sync_service.backend, GoogleDrivePersistence)
        )
        report["durable_runtime_connector_configured"] = bool(
            self.sync_service is not None and self.sync_service.runtime_state_capable
        )
        report["google_drive"] = {
            "host_connector_dependency": ("CAPABILITY_ROUTED_NOT_GLOBALLY_REQUIRED"),
            "connector_id": "connector_5f3c8c41a1e54ad7a76272c89e2554fa",
            "oauth_route": "NORMAL_HOST_CONNECTOR",
            "connector_token_exposed_to_plugin_mcp": False,  # nosec B105
            "connector_connection_state": (
                "HOST_OAUTH_OPTIONAL_UNTIL_PERSISTENCE_ROUTE_SELECTS_DRIVE"
            ),
            "connector_role": "OAUTH_ONBOARDING_AND_VERIFIED_MIRROR",
            "direct_server_backend_configured": bool(
                self.sync_service is not None
                and self.sync_service.runtime_state_capable
            ),
            "direct_server_backend_role": (
                "OPTIONAL_SEALED_ARTIFACT_MIRROR_NOT_PRIMARY_RUNTIME_AUTHORITY"
            ),
        }
        return report

    def session_flash_status(self) -> dict[str, Any]:
        return {
            **self.flash_authority.status(),
            "runtime_activation": self.runtime_activation.status_with_host_proof(),
        }

    def runtime_activation_status(self) -> dict[str, Any]:
        projection = self.runtime_activation.status_with_host_proof()
        configured_active = projection.get("state") == "ACTIVE"
        capture_complete = (
            projection.get("required_pre_reasoning_capture_complete") is True
        )
        indexed_by_session: list[dict[str, Any]] = []
        all_prompt_records = PromptIndex(self.store.root)._all_records()
        for active in projection.get("active_sessions", []):
            if not isinstance(active, dict):
                continue
            project_id = str(active.get("project_id") or "")
            evidence_session_id = str(active.get("session_id") or "")
            host_session_ids = {
                str(value) for value in active.get("host_session_ids", []) if str(value)
            }
            session_records = [
                row
                for row in all_prompt_records
                if row.get("project_id") == project_id
                and row.get("evidence_session_id") == evidence_session_id
                and row.get("host_session_id") in host_session_ids
            ]
            invoked_records = [
                row
                for row in session_records
                if isinstance(row.get("capture_dispatch"), dict)
                and row["capture_dispatch"].get("state")
                == "USERPROMPTSUBMIT_ADAPTER_INVOKED"
                and row["capture_dispatch"].get("adapter_invocation_observed") is True
                and row["capture_dispatch"].get("host_payload_hook_event_name")
                == "UserPromptSubmit"
            ]
            indexed_by_session.append(
                {
                    "project_id": project_id,
                    "evidence_session_id": evidence_session_id,
                    "bound_host_session_count": len(host_session_ids),
                    "indexed_visible_input_count": len(session_records),
                    "validated_adapter_invocation_count": len(invoked_records),
                    "latest_prompt_index": (
                        max(
                            int(row.get("prompt_index") or 0) for row in session_records
                        )
                        if session_records
                        else None
                    ),
                    "per_input_invocation_proven": bool(invoked_records),
                    "installed_host_dispatch_independently_proven": False,
                    "independent_host_proof_owner": (
                        "INSTALLED_HOST_ACCEPTANCE_CORRELATION"
                    ),
                }
            )
        active_without_capture = [
            row
            for row in indexed_by_session
            if int(row["validated_adapter_invocation_count"]) == 0
        ]
        capability_unavailable = bool(
            projection.get("host_capability_unavailable_surfaces")
        )
        hook_status = dict(projection.get("host_hook_status") or {})
        hook_integrity_mismatch = hook_status.get("status") == "MISMATCH"
        hooks_trusted = projection.get("host_hooks_trusted") is True
        hooks_enabled = projection.get("host_hooks_enabled") is True
        if not configured_active:
            activation_quality = "DETACHED"
        elif hook_integrity_mismatch:
            activation_quality = "RUNNABLE_HOOK_INTEGRITY_MISMATCH"
        elif hooks_trusted and not hooks_enabled:
            activation_quality = "RUNNABLE_HOOKS_OFF"
        elif active_without_capture:
            activation_quality = "RUNNABLE_PROMPT_INDEX_EVIDENCE_GAP"
        elif capability_unavailable or not capture_complete:
            activation_quality = "RUNNABLE_HOST_CAPABILITY_LIMITED"
        else:
            activation_quality = "FULLY_RUNNABLE"
        overall_status = "PASS" if configured_active else "FAIL"
        return {
            "status": overall_status,
            "activation_quality": activation_quality,
            "explicit_public_actions_runnable": configured_active,
            "prompt_response_capture_decoupled_from_hooks": True,
            "hook_lifecycle_strengthening_runnable": (
                hooks_trusted and hooks_enabled and capture_complete
            ),
            "hooks_off_is_structured_domain_state": True,
            "capture_unavailable_is_structured_domain_state": True,
            "per_session_capture_evidence": indexed_by_session,
            "active_session_capture_gap_count": len(active_without_capture),
            "active_session_capture_gap_code": (
                "ACTIVE_RUNTIME_WITHOUT_SEALED_PROMPT_INDEX_RECORD"
                if active_without_capture
                else None
            ),
            "runtime_flags_are_invocation_proof": False,
            "adapter_record_is_independent_installed_host_proof": False,
            **projection,
        }

    def transition_law(self) -> dict[str, Any]:
        return {"status": "PASS", **transition_catalog()}

    def lane_catalog(self) -> dict[str, Any]:
        return self.lane_reader.lane_catalog()

    def _connector_governance(self, project_id: str) -> ConnectorGovernance:
        self.store.config(project_id)
        return ConnectorGovernance(
            self.store.project_root(project_id)
            / "connector_brain"
            / "connector-brain.sqlite"
        )

    def connector_plugin_register(
        self, project_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).register(**kwargs)

    def connector_plugin_drop(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._connector_governance(project_id).drop(**kwargs)

    def connector_plugin_catalog(self, project_id: str) -> dict[str, Any]:
        return self._connector_governance(project_id).catalog()

    def connector_plugin_settings(
        self, project_id: str, *, host_profile: str
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).settings(
            host_profile=host_profile
        )

    def connector_plugin_route(
        self,
        project_id: str,
        *,
        capability: str,
        canonical_lane_id: str | None = None,
        host_profile: str = "CODEX",
        preferred_plugin_id: str | None = None,
    ) -> dict[str, Any]:
        return self._connector_governance(project_id).route(
            capability=capability,
            canonical_lane_id=canonical_lane_id,
            host_profile=host_profile,
            preferred_plugin_id=preferred_plugin_id,
        )

    def classify_hil_intent(
        self,
        project_id: str,
        session_id: str,
        utterance: str,
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.load(project_id, session_id)
        pending_hil = session.state.value in {"PV1_CANDIDATE", "PVN1_CANDIDATE"}
        result = classify_hil_intent(
            utterance,
            candidate_id=session.candidate_id,
            pending_hil=pending_hil,
        )
        receipt = self.sessions.record_hil_intent(
            project_id,
            session_id,
            classification={**result, "visible_utterance": utterance},
            event_id=event_id,
        )
        result["chat_lineage"] = {
            "append_status": "APPENDED",
            "event_id": receipt["event"]["event_id"],
            "event_sha256": receipt["event"]["event_sha256"],
        }
        return result

    def source_intake(
        self,
        project_id: str,
        sources: list[str],
        *,
        overrides: dict[str, str] | None = None,
        session_id: str | None = None,
        git_mode: str = "AUTO",
        authority_mode: str = "CLASSIFICATION_ONLY",
        source_assertions: dict[str, dict[str, Any]] | None = None,
        turn_entry: dict[str, Any] | None = None,
        plan_dispatch: dict[str, Any] | None = None,
        working_authority_action: str = "CLASSIFY_ONLY",
    ) -> dict[str, Any]:
        """Classify ordered sources through one generalized public control."""

        config = self.store.config(project_id)
        # Source Intake reads the current workspace.  A GitHub remote does not
        # turn dirty local bytes into GitHub authority; the dedicated Git
        # history/checkpoint routes own github_code.
        inspect_repository(config.repository_path)
        code_lane = "local_code"
        result = classify_source_intake(
            sources,
            code_mode=code_lane,
            overrides=overrides,
            git_mode=git_mode,
            authority_mode=authority_mode,
            authority_registry_path=(
                self.store.source_authority_path(project_id)
                if authority_mode.strip().upper() == "GOVERNED_CONTENT_REGISTRY"
                else None
            ),
            source_assertions=source_assertions,
            registered_repository_path=config.repository_path,
        )
        active_session_id = session_id.strip() if session_id else ""
        if not active_session_id:
            active_path = self.store.project_root(project_id) / "active_session.json"
            if active_path.is_file():
                active_session_id = str(
                    json.loads(active_path.read_text(encoding="utf-8")).get(
                        "session_id"
                    )
                    or ""
                )
        normalized_working_action = working_authority_action.strip().upper()
        require(
            normalized_working_action in {"CLASSIFY_ONLY", "REFRESH_WORKING_SECTORS"},
            "SOURCE_INTAKE_WORKING_ACTION_INVALID",
            "Source Intake working authority action must be CLASSIFY_ONLY or "
            "REFRESH_WORKING_SECTORS.",
            status="BLOCKED",
        )
        if normalized_working_action == "REFRESH_WORKING_SECTORS":
            require(
                bool(active_session_id) and turn_entry is None,
                "SOURCE_INTAKE_REFRESH_BOUNDARY_INVALID",
                "Explicit WORKING-sector refresh requires the exact active session "
                "and must be separate from a turn-entry query.",
                status="BLOCKED",
            )
            pointer = self.store.pointer(project_id)
            accepted_pv = str(pointer.accepted_pv or "").strip()
            pv0_bootstrap = False
            if not accepted_pv:
                bootstrap_session = self.sessions.load(project_id, active_session_id)
                pv0_bootstrap = (
                    self.store.uses_external_project_authority(project_id)
                    and pointer.generation == 0
                    and bootstrap_session.state == SessionState.BOOTED
                    and bootstrap_session.accepted_pv is None
                    and bootstrap_session.candidate_id is None
                )
                require(
                    pv0_bootstrap,
                    "SOURCE_INTAKE_REFRESH_ACCEPTED_PV_REQUIRED",
                    "WORKING-sector refresh requires an accepted baseline, except "
                    "for the exact live-root PV0 bootstrap owned by initial Build.",
                    status="BLOCKED",
                )
                accepted_pv = "PV0"
            repository = inspect_repository(config.repository_path)
            result["working_authority_refresh"] = migrate_working_project_sectors(
                self.store.project_root(project_id),
                repository_root=config.repository_path,
                project_id=project_id,
                accepted_pv=accepted_pv,
                pointer_generation=pointer.generation,
                expected_branch=repository.branch,
                expected_head=repository.commit_sha,
                bootstrap_pv0=pv0_bootstrap,
            )
            result["working_authority_refresh"]["pv0_bootstrap_pending"] = (
                pv0_bootstrap
            )
        else:
            result["working_authority_refresh"] = {
                "status": "NOT_PERFORMED",
                "reason": "CLASSIFICATION_OR_READ_ONLY_QUERY_BOUNDARY",
            }
        if active_session_id and turn_entry is None:
            receipt = self.sessions.record_source_intake_classification(
                project_id,
                active_session_id,
                classification=result,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": receipt["event"]["event_id"],
            }
            result["prior_lifecycle_state"] = receipt["lifecycle_state_unchanged"]
            result["pointer"] = receipt["pointer"]
        elif not active_session_id:
            result["chat_lineage"] = {"append_status": "NO_ACTIVE_SESSION"}
            result["prior_lifecycle_state"] = "NO_ACTIVE_SESSION"
        else:
            result["chat_lineage"] = {
                "append_status": "DEFERRED_UNTIL_TURN_ENTRY_PREFLIGHT"
            }
            result["prior_lifecycle_state"] = "PREFLIGHT_PENDING"
        result["next_action"] = "RETURN_TO_SOURCE_INTAKE_OR_PRIOR_LIFECYCLE_POSITION"
        if turn_entry is not None:
            require(
                normalized_working_action == "CLASSIFY_ONLY",
                "TURN_ENTRY_QUERY_MUST_NOT_REFRESH",
                "A turn-entry query cannot refresh or migrate project authority.",
                status="BLOCKED",
            )
            require(
                active_session_id != "" and isinstance(turn_entry, dict),
                "TURN_ENTRY_SESSION_REQUIRED",
                "A formula-bound turn entry requires the exact active session.",
                status="BLOCKED",
            )
            session = self.sessions.load(project_id, active_session_id)
            runtime_task = dict(session.task or {})
            metadata_active_task_id = str(
                session.metadata.get("active_backlog_task_id") or ""
            ).strip()
            runtime_backlog_task_id = str(
                runtime_task.get("backlog_task_id") or metadata_active_task_id
            ).strip()
            backlog_snapshot = self.store.backlog_status(project_id)
            live_active_task_ids = [
                str(task["task_id"])
                for task in backlog_snapshot.get("tasks", [])
                if task.get("status") == "ACTIVE"
            ]
            active_task_id = str(
                turn_entry.get("task_id") or runtime_backlog_task_id or ""
            ).strip()
            require(
                bool(active_task_id)
                and active_task_id == runtime_backlog_task_id
                and live_active_task_ids == [active_task_id],
                "TURN_ENTRY_ACTIVE_TASK_MISMATCH",
                "Turn entry must bind the exact session task and sole active Plan row.",
                status="MISMATCH",
                active_task_id=active_task_id,
                runtime_backlog_task_id=runtime_backlog_task_id,
                metadata_active_task_id=metadata_active_task_id,
                live_active_task_ids=live_active_task_ids,
            )
            bounded_query = str(turn_entry.get("bounded_query") or "").strip()
            formula = turn_entry.get("formula")
            require(
                bool(bounded_query)
                and len(bounded_query.encode("utf-8")) <= 4096
                and isinstance(formula, dict)
                and 0 < len(canonical_json_bytes(formula)) <= 16 * 1024,
                "TURN_ENTRY_BOUNDED_INPUT_INVALID",
                "Turn entry requires one bounded query and a compact formula record.",
                status="BLOCKED",
            )
            formula = cast(dict[str, Any], formula)
            required_formula_fields = {
                "fired_modes",
                "operators",
                "bounded_source_locators",
                "sector_locators",
                "env_uop_terms",
                "assumptions",
                "intended_validator",
                "expected_result",
                "formula_expression",
            }
            missing_formula_fields = sorted(required_formula_fields - set(formula))
            bounded_list_fields = {
                "fired_modes",
                "operators",
                "bounded_source_locators",
                "sector_locators",
                "env_uop_terms",
                "assumptions",
            }
            invalid_list_fields = sorted(
                field
                for field in bounded_list_fields
                if not isinstance(formula.get(field), list)
                or not 0 < len(formula[field]) <= 32
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in formula[field]
                )
            )
            invalid_scalar_fields = sorted(
                field
                for field in (
                    "intended_validator",
                    "expected_result",
                    "formula_expression",
                )
                if not isinstance(formula.get(field), str) or not formula[field].strip()
            )
            invalid_formula_sector_locators = sorted(
                {
                    value
                    for value in formula.get("sector_locators", [])
                    if value not in CANONICAL_LANE_IDS
                }
            )
            require(
                not missing_formula_fields
                and not invalid_list_fields
                and not invalid_scalar_fields,
                "TURN_ENTRY_FORMULA_SCHEMA_INVALID",
                "The task formula must carry the bounded source, mode, operator, "
                "ENV/UOP, assumption, validator, result, and expression terms.",
                status="BLOCKED",
                missing_formula_fields=missing_formula_fields,
                invalid_list_fields=invalid_list_fields,
                invalid_scalar_fields=invalid_scalar_fields,
            )
            require(
                not invalid_formula_sector_locators,
                "TURN_ENTRY_FORMULA_SECTOR_INVALID",
                "Task-formula sector locators must use canonical lane IDs.",
                status="BLOCKED",
                invalid_formula_sector_locators=invalid_formula_sector_locators,
            )
            pointer = self.store.pointer(project_id)
            accepted_pv = str(pointer.accepted_pv or "").strip()
            require(
                bool(accepted_pv),
                "TURN_ENTRY_ACCEPTED_PV_REQUIRED",
                "Turn entry requires one accepted PV baseline.",
                status="BLOCKED",
            )
            repository = inspect_repository(config.repository_path)
            working = query_working_project_sectors(
                self.store.project_root(project_id),
                repository_root=config.repository_path,
                project_id=project_id,
                accepted_pv=accepted_pv,
                pointer_generation=pointer.generation,
                query=bounded_query,
                lane_ids=list(
                    dict.fromkeys(
                        [
                            lane_id
                            for lane_id in result["ordered_canonical_lanes"]
                            if lane_id in CANONICAL_LANE_IDS
                        ]
                        + list(formula["sector_locators"])
                        + ["plan"]
                    )
                ),
                limit=int(turn_entry.get("limit") or 8),
                expected_branch=repository.branch,
                expected_head=repository.commit_sha,
            )
            working_freshness = evaluate_working_lane_freshness(
                self.store,
                project_id,
                self.store.project_root(project_id) / "sectors",
                bounded_dirty_read=True,
            )
            requested_source_event_id = str(
                turn_entry.get("source_event_id") or ""
            ).strip()
            if not requested_source_event_id:
                source_event_seed = {
                    "project_id": project_id,
                    "session_id": active_session_id,
                    "task_id": active_task_id,
                    "formula_event_id": str(turn_entry.get("event_id") or ""),
                    "formula_sha256": sha256_bytes(canonical_json_bytes(formula)),
                    "classification_sha256": sha256_bytes(canonical_json_bytes(result)),
                }
                requested_source_event_id = (
                    "source_intake_"
                    + sha256_bytes(canonical_json_bytes(source_event_seed))[:32].lower()
                )
            source_receipt = self.sessions.record_source_intake_classification(
                project_id,
                active_session_id,
                classification=result,
                event_id=requested_source_event_id,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED_OR_REPLAYED",
                "event_id": source_receipt["event"]["event_id"],
            }
            result["prior_lifecycle_state"] = source_receipt[
                "lifecycle_state_unchanged"
            ]
            result["pointer"] = source_receipt["pointer"]
            source_event_id = str(source_receipt["event"]["event_id"])
            formula_receipt = self.store.record_task_formula(
                project_id,
                task_id=active_task_id,
                event_kind=str(turn_entry.get("event_kind") or "ENTRY_FORMULA"),
                source_event_id=source_event_id,
                session_id=active_session_id,
                formula=formula,
                actor=str(turn_entry.get("actor") or "TURN_ENTRY_SOURCE_INTAKE"),
                prior_formula_sha256=(
                    str(turn_entry["prior_formula_sha256"])
                    if turn_entry.get("prior_formula_sha256")
                    else None
                ),
                changed_terms=(
                    dict(turn_entry.get("changed_terms") or {})
                    if turn_entry.get("changed_terms") is not None
                    else None
                ),
                cause_evidence_locator=(
                    str(turn_entry["cause_evidence_locator"])
                    if turn_entry.get("cause_evidence_locator")
                    else None
                ),
                event_id=(
                    str(turn_entry["event_id"]) if turn_entry.get("event_id") else None
                ),
            )
            result["turn_entry"] = {
                "status": "PASS",
                "schema": "evidence-lane.turn-entry-intake.v1",
                "active_task_id": active_task_id,
                "source_event_id": source_event_id,
                "formula_event_id": formula_receipt["event"]["event_id"],
                "formula_sha256": formula_receipt["event"]["formula_sha256"],
                "formula_event_kind": formula_receipt["event"]["event_kind"],
                "working_sector_query": working,
                "decision_routing": {
                    "plan_runtime_authority_state": (
                        "LIVE_CURRENT_EXECUTION_AUTHORITY"
                    ),
                    "auxiliary_authorities": [
                        "AGENT_LEARNING",
                        "PROJECT_MEMORY",
                        "CANON_GRAPH",
                    ],
                    "working_sector_fallback_states": [
                        "STALE",
                        "NO_HIT",
                        "EMPTY",
                        "INCOMPLETE",
                    ],
                    "working_sector_query_executed": True,
                    "instruction_authorities_separate": [
                        "AGENTS.md",
                        "MEMORY.md",
                    ],
                    "authority_merge_allowed": False,
                },
                "live_root_freshness": working_freshness,
                "fallback_authority": "LIVE_ROOT_ALL_18_SECTORS",
                "accepted_archive_opened": False,
                "accepted_archive_queried": False,
                "accepted_pointer_used_as_baseline_only": True,
                "raw_prompt_persisted": False,
                "raw_plan_loaded": False,
                "raw_pv_loaded": False,
                "raw_chat_lineage_loaded": False,
                "private_reasoning_stored": False,
                "candidate_created": False,
                "pointer_moved": False,
                "hil_inferred": False,
            }
        if plan_dispatch is not None:
            require(
                isinstance(plan_dispatch, dict) and turn_entry is None,
                "SOURCE_INTAKE_PLAN_DISPATCH_INVALID",
                "Prompt Plan dispatch must be one separate structured classification.",
                status="BLOCKED",
            )
            dispatch_class = str(
                plan_dispatch.get("classification") or ""
            ).strip().upper()
            require(
                dispatch_class in {"ORDINARY", "EXECUTION_CHANGING"},
                "SOURCE_INTAKE_PLAN_DISPATCH_CLASS_INVALID",
                "Prompt Plan dispatch must be ORDINARY or EXECUTION_CHANGING.",
                status="BLOCKED",
            )
            if dispatch_class == "ORDINARY":
                require(
                    not any(
                        plan_dispatch.get(field)
                        for field in (
                            "delta_id",
                            "delta_text",
                            "linked_task_id",
                            "boundary",
                        )
                    ),
                    "SOURCE_INTAKE_ORDINARY_PLAN_FIELDS_FORBIDDEN",
                    "Questions and status requests cannot carry Plan-steer fields.",
                    status="BLOCKED",
                )
                result["plan_dispatch"] = {
                    "status": "PASS",
                    "classification": "ORDINARY",
                    "pv_plan_steer_delta_invoked": False,
                    "plan_mutated": False,
                    "chat_lineage_only": True,
                }
            else:
                exact_delta_id = str(plan_dispatch.get("delta_id") or "").strip()
                exact_delta_text = str(plan_dispatch.get("delta_text") or "").strip()
                exact_linked_task_id = str(
                    plan_dispatch.get("linked_task_id") or ""
                ).strip()
                exact_actor = str(plan_dispatch.get("actor") or "").strip()
                changed_fields = sorted(
                    {
                        str(field).strip().upper()
                        for field in plan_dispatch.get("changed_fields") or []
                        if str(field).strip()
                    }
                )
                allowed_changed_fields = {
                    "OUTCOME",
                    "DEPENDENCY",
                    "ACCEPTANCE",
                    "STOP_CONDITION",
                    "RELEASE_ROUTE",
                    "HIL_PATH",
                }
                require(
                    bool(active_session_id)
                    and bool(exact_delta_id)
                    and bool(exact_delta_text)
                    and bool(exact_linked_task_id)
                    and bool(exact_actor)
                    and bool(changed_fields)
                    and set(changed_fields).issubset(allowed_changed_fields),
                    "SOURCE_INTAKE_EXECUTION_STEER_REQUIRED",
                    "Execution-changing prompts require stable steer identity, exact linked task, actor, text, and changed contract fields.",
                    status="BLOCKED",
                )
                steer = self.record_steer_delta(
                    project_id,
                    delta_id=exact_delta_id,
                    delta_text=exact_delta_text,
                    actor=exact_actor,
                    boundary=str(
                        plan_dispatch.get("boundary") or "BEFORE_NEXT_HIL"
                    ),
                    linked_task_id=exact_linked_task_id,
                    new_task_contract=None,
                )
                result["plan_dispatch"] = {
                    "status": "PASS",
                    "classification": "EXECUTION_CHANGING",
                    "changed_fields": changed_fields,
                    "pv_plan_steer_delta_invoked": True,
                    "plan_steer": steer,
                    "stable_idempotent_id": exact_delta_id,
                    "source_intake_preceded_plan_steer": True,
                    "chat_lineage_event_id": result.get("chat_lineage", {}).get(
                        "event_id"
                    ),
                }
        result["agent_configuration"] = (
            self._active_agent_configuration_authority(project_id)
            if active_session_id
            else None
        )
        result["conversation_memory"] = (
            self._active_conversation_memory_authority(project_id)
            if active_session_id
            else None
        )
        return result

    def adaptive_delta_exit(
        self,
        project_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run one governed per-Delta exit without advancing the Plan row."""

        return run_adaptive_delta_exit(
            self,
            project_id,
            session_id,
            **kwargs,
        )

    def adaptive_delta_entry(
        self,
        project_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Consume the live-root authorities before one Delta begins source work."""

        return run_adaptive_delta_entry(
            self,
            project_id,
            session_id,
            **kwargs,
        )

    def classify_and_enter_delta(
        self,
        project_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Classify through the current route and auto-fire first-class Delta entry."""

        classified = self.sessions.classify(
            project_id,
            session_id,
            **kwargs,
        )
        require(
            classified.get("status") == "PASS",
            "ADAPTIVE_DELTA_ENTRY_CLASSIFICATION_FAILED",
            "Delta entry cannot run after a failed task classification.",
            status="FAIL",
        )
        classified["adaptive_delta_entry"] = self.adaptive_delta_entry(
            project_id,
            session_id,
            classification_result=classified,
        )
        return classified

    def _refresh_delta_source_authority(
        self,
        project_id: str,
        session_id: str,
        *,
        task_id: str,
    ) -> dict[str, Any]:
        """Refresh all canonical working lanes while preserving the Git baseline."""

        exact_task_id = str(task_id or "").strip()
        session = self.sessions.load(project_id, session_id)
        session_task_id = str(
            session.metadata.get("active_backlog_task_id") or ""
        ).strip()
        backlog = self.store.backlog_status(project_id)
        active_task_ids = [
            str(row["task_id"])
            for row in backlog["goal_projection"]["rows"]
            if row.get("status") == "in_progress"
        ]
        require(
            bool(exact_task_id)
            and session_task_id == exact_task_id
            and active_task_ids == [exact_task_id],
            "DELTA_SOURCE_REFRESH_TASK_BINDING_MISMATCH",
            "Delta source refresh requires the exact session task and sole active Plan row.",
            status="MISMATCH",
            requested_task_id=exact_task_id or None,
            session_task_id=session_task_id or None,
            active_task_ids=active_task_ids,
        )
        pointer = self.store.pointer(project_id)
        accepted_pv = str(pointer.accepted_pv or "").strip()
        require(
            bool(accepted_pv),
            "DELTA_SOURCE_REFRESH_ACCEPTED_PV_REQUIRED",
            "Delta source refresh requires an accepted historical baseline.",
            status="MISMATCH",
        )
        repository_path = self.store.config(project_id).repository_path
        repository_before = inspect_repository(repository_path).as_dict()
        file_manifest_before = tracked_worktree_file_manifest(repository_path)
        dirty_identity_before = calculate_worktree_change_identity(repository_path)
        local_refresh = migrate_working_project_sectors(
            self.store.project_root(project_id),
            repository_root=repository_path,
            project_id=project_id,
            accepted_pv=accepted_pv,
            pointer_generation=pointer.generation,
            expected_branch=repository_before["branch"],
            expected_head=repository_before["commit_sha"],
        )
        repository_after = inspect_repository(repository_path).as_dict()
        file_manifest_after = tracked_worktree_file_manifest(repository_path)
        dirty_identity_after = calculate_worktree_change_identity(repository_path)
        identity_fields = ("branch", "commit_sha", "tree_sha", "worktree_sha256")
        require(
            local_refresh.get("status") == "PASS"
            and all(
                repository_before.get(field) == repository_after.get(field)
                for field in identity_fields
            ),
            "DELTA_SOURCE_REFRESH_REPOSITORY_DRIFT",
            "Local Code refresh changed or crossed the exact Git/worktree baseline.",
            status="MISMATCH",
        )
        require(
            file_manifest_after == file_manifest_before,
            "DELTA_SOURCE_REFRESH_FILE_FINGERPRINT_DRIFT",
            "The per-file tracked source manifest changed during Delta exit refresh.",
            status="MISMATCH",
        )
        require(
            dirty_identity_after == dirty_identity_before,
            "DELTA_SOURCE_REFRESH_DIRTY_BYTE_IDENTITY_DRIFT",
            "The tracked-dirty or untracked byte identity changed during Delta exit refresh.",
            status="MISMATCH",
        )
        fingerprint_root = (
            self.store.project_root(project_id)
            / "receipts"
            / "delta-source-fingerprints"
        )
        fingerprint_path = (
            fingerprint_root
            / f"{file_manifest_before['receipt_sha256'].lower()}.json"
        )
        if fingerprint_path.is_file():
            require(
                json.loads(fingerprint_path.read_text(encoding="utf-8"))
                == file_manifest_before,
                "DELTA_SOURCE_REFRESH_FILE_FINGERPRINT_CONFLICT",
                "The content-addressed per-file fingerprint receipt has different bytes.",
                status="MISMATCH",
            )
        else:
            atomic_write_json(fingerprint_path, file_manifest_before)
        per_file_fingerprint = {
            key: file_manifest_before[key]
            for key in (
                "schema",
                "status",
                "selection",
                "tracked_path_count",
                "current_file_count",
                "current_symlink_count",
                "tracked_deleted_count",
                "path_set_sha256",
                "file_manifest_sha256",
                "receipt_sha256",
                "untracked_paths_included",
                "ignored_paths_included",
                "remote_git_mutated",
                "git_index_mutated",
                "git_ref_mutated",
            )
        }
        per_file_fingerprint["receipt_path"] = str(fingerprint_path)
        per_file_fingerprint["full_entry_manifest_returned"] = False
        dirty_byte_identity = {
            key: dirty_identity_before.get(key)
            for key in (
                "dirty_path_count",
                "dirty_path_set_sha256",
                "dirty_content_sha256",
                "tracked_dirty_path_count",
                "tracked_dirty_content_sha256",
                "untracked_path_count",
                "untracked_content_sha256",
                "status_sha256",
                "working_identity_sha256",
                "raw_paths_persisted",
            )
        }
        dirty_byte_identity["identity_unchanged_during_refresh"] = True
        local_body = {
            "lane_id": "local_code",
            "status": "PASS",
            "state": local_refresh.get("state"),
            "authority_receipt_sha256": local_refresh.get("receipt_sha256"),
            "working_identity_sha256": dict(
                local_refresh.get("working_identity") or {}
            ).get("working_identity_sha256"),
            "local_only_evidence_content_read": False,
            "candidate_created": False,
            "pointer_moved": False,
        }
        local_receipt = {
            **local_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(local_body)),
        }
        git_body = {
            "lane_id": "github_code",
            "status": "PASS",
            "state": "EXACT_GIT_BASELINE_PRESERVED",
            "branch": repository_after.get("branch"),
            "commit_sha": repository_after.get("commit_sha"),
            "tree_sha": repository_after.get("tree_sha"),
            "git_mutated": False,
            "remote_sync_performed": False,
        }
        git_receipt = {
            **git_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(git_body)),
        }
        canonical_lane_refresh = dict(local_refresh.get("canonical_lane_refresh") or {})
        body = {
            "schema": "evidence-lane.delta-source-authority-refresh.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session_id,
            "task_id": exact_task_id,
            "source_planes": [local_receipt, git_receipt],
            "canonical_lane_refresh": canonical_lane_refresh,
            "dedicated_authorities_separate": [
                "agent_learning",
                "canon",
                "project_memory",
                "project_universe",
                "connector_brain",
                "resolved_instruction_sources",
            ],
            "repository_identity_unchanged": True,
            "per_file_tracked_source_fingerprint": per_file_fingerprint,
            "dirty_and_untracked_byte_identity": dirty_byte_identity,
            "candidate_created": False,
            "pointer_moved": False,
            "git_mutated": False,
        }
        return {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def source_sqlite_inspect(
        self,
        project_id: str,
        batch_id: str,
        *,
        session_id: str | None = None,
        max_embedded_member_bytes: int = 768 * 1024 * 1024,
        exact_count_max_database_bytes: int = 32 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Inspect registered direct and embedded SQLite assets read-only."""

        self.store.config(project_id)
        result = inspect_registered_sqlite_assets(
            self.store.source_authority_path(project_id),
            batch_id,
            max_embedded_member_bytes=max_embedded_member_bytes,
            exact_count_max_database_bytes=exact_count_max_database_bytes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_sqlite_{str(result['event_sha256'])[:32].lower()}",
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"failure_samples"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_custom_schema_compile(
        self,
        project_id: str,
        batch_id: str,
        schema_definition: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Compile and map one declarative Custom Source Schema."""

        self.store.config(project_id)
        result = compile_and_map_custom_source_schema(
            self.store.source_authority_path(project_id),
            batch_id,
            schema_definition,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_custom_schema_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"mappings"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_intake_schema_configure(
        self,
        project_id: str,
        batch_id: str,
        *,
        operation: str,
        pill_name: str,
        schema_definition: dict[str, Any],
        expected_previous_schema_sha256: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Add or version one governed schema-derived Source Intake pill."""

        self.store.config(project_id)
        result = configure_source_intake_schema_pill(
            self.store.source_authority_path(project_id),
            batch_id,
            operation=operation,
            pill_name=pill_name,
            definition=schema_definition,
            expected_previous_schema_sha256=expected_previous_schema_sha256,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_intake_schema_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload={
                    "operation": result["operation"],
                    "configuration_status": result["configuration_status"],
                    "pill_projection": result["pill_projection"],
                    "receipt_id": result["receipt_id"],
                    "receipt_sha256": result["receipt_sha256"],
                    "candidate_created": False,
                    "pointer_moved": False,
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_identity_register(
        self,
        project_id: str,
        batch_id: str,
        *,
        entities: list[dict[str, Any]],
        profiles: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a complete, non-conflating source identity matrix."""

        self.store.config(project_id)
        result = register_source_identity_matrix(
            self.store.source_authority_path(project_id),
            batch_id,
            entities=entities,
            profiles=profiles,
            relations=relations,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_identity_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload=result,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_graph_build(
        self,
        project_id: str,
        batch_id: str,
        *,
        occurrence_ordinals: list[int] | None = None,
        member_path_prefixes: list[str] | None = None,
        max_files: int = 25_000,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_nodes: int = 500_000,
        max_edges: int = 1_000_000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a bounded provenance-first graph over registered source bytes."""

        self.store.config(project_id)
        result = build_registered_source_graph(
            self.store.source_authority_path(project_id),
            batch_id,
            occurrence_ordinals=occurrence_ordinals,
            member_path_prefixes=member_path_prefixes,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_graph_{str(result['receipt_sha256'])[:32].lower()}",
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"node_samples", "edge_samples"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_graph_diff(
        self,
        project_id: str,
        from_graph_id: str,
        to_graph_id: str,
        *,
        sample_limit: int = 100,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Diff two exact registered source-graph snapshots."""

        self.store.config(project_id)
        result = diff_source_graphs(
            self.store.source_authority_path(project_id),
            from_graph_id,
            to_graph_id,
            sample_limit=sample_limit,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_graph_diff_{str(result['receipt_sha256'])[:32].lower()}",
                visible_payload={
                    key: value for key, value in result.items() if key != "samples"
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_graph_impact(
        self,
        project_id: str,
        graph_id: str,
        seed_node_ids: list[str],
        *,
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Traverse one bounded affected subgraph from exact node IDs."""

        self.store.config(project_id)
        result = source_graph_impact(
            self.store.source_authority_path(project_id),
            graph_id,
            seed_node_ids,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=f"source_graph_impact_{str(result['receipt_sha256'])[:32].lower()}",
                visible_payload={
                    key: value for key, value in result.items() if key != "nodes"
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_git_history_build(
        self,
        project_id: str,
        batch_id: str,
        occurrence_ordinal: int,
        *,
        max_refs: int = 20_000,
        max_commits: int = 100_000,
        max_objects: int = 2_000_000,
        max_tree_entries: int = 5_000_000,
        max_file_changes: int = 2_000_000,
        max_hunks: int = 2_000_000,
        max_changed_lines: int = 5_000_000,
        max_patch_bytes: int = 2 * 1024 * 1024 * 1024,
        max_single_object_bytes: int = 1024 * 1024 * 1024,
        max_total_object_bytes: int = 8 * 1024 * 1024 * 1024,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Seal full, bounded, read-only Git evidence for one source occurrence."""

        self.store.config(project_id)
        result = build_registered_git_history(
            self.store.source_authority_path(project_id),
            batch_id,
            occurrence_ordinal,
            max_refs=max_refs,
            max_commits=max_commits,
            max_objects=max_objects,
            max_tree_entries=max_tree_entries,
            max_file_changes=max_file_changes,
            max_hunks=max_hunks,
            max_changed_lines=max_changed_lines,
            max_patch_bytes=max_patch_bytes,
            max_single_object_bytes=max_single_object_bytes,
            max_total_object_bytes=max_total_object_bytes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_git_history_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload=result,
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def source_git_commit_impact(
        self,
        project_id: str,
        snapshot_id: str,
        graph_id: str,
        commit_sha: str,
        *,
        parent_ordinal: int = 0,
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Bind an exact Git parent-diff to a bounded semantic impact graph."""

        self.store.config(project_id)
        result = build_source_git_commit_impact(
            self.store.source_authority_path(project_id),
            snapshot_id,
            graph_id,
            commit_sha,
            parent_ordinal=parent_ordinal,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        if session_id:
            activity = self.sessions.record_activity(
                project_id,
                session_id,
                activity_type="build.output",
                event_id=(
                    f"source_git_impact_{str(result['receipt_sha256'])[:32].lower()}"
                ),
                visible_payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"changed_paths", "mapped_paths", "unmapped_paths"}
                },
            )
            result["chat_lineage"] = {
                "append_status": "APPENDED",
                "event_id": activity["event"]["event_id"],
                "event_sha256": activity["event"]["event_sha256"],
            }
        else:
            result["chat_lineage"] = {"append_status": "NO_SESSION_REQUESTED"}
        return result

    def classify_mode(
        self,
        project_id: str,
        request: str,
        *,
        explicit_modes: list[str] | None = None,
        session_id: str | None = None,
        custom_modes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Classify an ENV15 mode intersection and its canonical lanes."""

        config = self.store.config(project_id)
        repository = inspect_repository(config.repository_path)
        code_lane = config.source_lane
        if code_lane not in {"github_code", "local_code"}:
            code_lane = (
                "github_code" if repository.provider == "github" else "local_code"
            )
        result = classify_operating_modes(
            request,
            explicit_modes=explicit_modes,
            code_lane=code_lane,
            custom_modes=custom_modes,
        )
        active_session_id = session_id.strip() if session_id else ""
        if not active_session_id:
            active_path = self.store.project_root(project_id) / "active_session.json"
            if active_path.is_file():
                active = json.loads(active_path.read_text(encoding="utf-8"))
                active_session_id = str(active.get("session_id") or "")
        if active_session_id:
            receipt = self.sessions.record_mode_classification(
                project_id,
                active_session_id,
                classification=result,
            )
            result["chat_lineage"]["append_status"] = "APPENDED"
            result["chat_lineage"]["event_id"] = receipt["event"]["event_id"]
            result["mode_binding"] = receipt["mode_binding"]
            result["prior_lifecycle_state"] = receipt["lifecycle_state_unchanged"]
            result["pointer"] = receipt["pointer"]
            if "PL" in {item["id"] for item in result["selected_modes"]}:
                result["plan_runtime"] = self.store.record_planning_mode(
                    project_id,
                    source_event_id=receipt["event"]["event_id"],
                    session_id=active_session_id,
                    request_sha256=sha256_bytes(
                        str(result.get("request", "")).encode("utf-8")
                    ),
                    selected_mode_ids=[item["id"] for item in result["selected_modes"]],
                    mode_intersection=result["mode_intersection"],
                    canonical_lanes=result["canonical_lanes"],
                    lifecycle_state=receipt["lifecycle_state_unchanged"],
                    pointer_generation=int(receipt["pointer"]["generation"]),
                )
            else:
                result["plan_runtime"] = {
                    "status": "NOT_SELECTED",
                    "append_status": "NOT_APPLICABLE",
                    "canonical_plan_sector_mutated": False,
                    "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
                }
        else:
            result["chat_lineage"]["append_status"] = "NO_ACTIVE_SESSION"
            result["prior_lifecycle_state"] = "NO_ACTIVE_SESSION"
            result["plan_runtime"] = {
                "status": "NO_ACTIVE_SESSION",
                "append_status": "NOT_APPENDED",
                "canonical_plan_sector_mutated": False,
                "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
            }
        result["next_action"] = "RETURN_TO_PRIOR_LIFECYCLE_POSITION"
        return result

    def lane_status(
        self,
        project_id: str,
        lane: str,
    ) -> dict[str, Any]:
        result = self.lane_reader.lane_status(project_id, lane)
        result["live_root_authority_ref"] = result.pop("pv_ref")
        if result.get("lane", {}).get("canonical_lane_id") == "plan":
            result["runtime_projection"] = self.store.plan_runtime_status(project_id)
            result["runtime_projection_authority"] = "TASK_BACKLOG_EVENT_LEDGER"
            result["canonical_plan_sector_mutated"] = False
        return result

    def lane_search(
        self,
        project_id: str,
        lane: str,
        query: str,
        *,
        limit: int = 20,
        retrieval: str = "hybrid",
    ) -> dict[str, Any]:
        result = self.lane_reader.search(
            project_id,
            lane,
            query,
            limit=limit,
            retrieval=retrieval,
        )
        result["live_root_authority_ref"] = result.pop("pv_ref")
        return result

    def lane_fetch(
        self,
        project_id: str,
        lane: str,
        path: str,
        *,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        result = self.lane_reader.fetch_source(
            project_id,
            lane,
            path,
            max_bytes=max_bytes,
        )
        result["live_root_authority_ref"] = result.pop("pv_ref")
        return result

    def live_authority_search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 8,
        session_id: str | None = None,
        refresh_on_miss: bool = True,
    ) -> dict[str, Any]:
        """Run the ENV/UOP-governed live-root six-way prompt/query route."""

        from .live_authority_query import query_live_authorities

        return query_live_authorities(
            self,
            project_id=project_id,
            session_id=session_id,
            query=query,
            limit=limit,
            refresh_on_miss=refresh_on_miss,
        )

    def configure_lane_routes(
        self,
        project_id: str,
        session_id: str,
        *,
        overrides: dict[str, str],
        granted_by: str,
        grant_id: str | None = None,
    ) -> dict[str, Any]:
        return self.sessions.configure_source_lanes(
            project_id,
            session_id,
            overrides=overrides,
            granted_by=granted_by,
            grant_id=grant_id,
        )

    def status(self, project_id: str) -> dict[str, Any]:
        """Return the durable accepted/candidate/session envelope without mutation."""
        result = self.store.project_status(project_id)
        pointer = self.store.pointer(project_id)
        external_project_authority = self.store.uses_external_project_authority(
            project_id
        )
        accepted_ids = (
            [pointer.accepted_pv]
            if external_project_authority and pointer.accepted_pv
            else self.store.accepted_ids(project_id)
        )
        if pointer.accepted_pv and pointer.accepted_pv not in accepted_ids:
            accepted_ids.append(pointer.accepted_pv)
            accepted_ids.sort(key=lambda value: int(value[2:]))
        accepted_validations: list[dict[str, Any]] = []
        if accepted_ids:
            # Accepted PVs are independent immutable directories. Validate them
            # concurrently so status keeps full checksum/tamper detection without
            # serially re-reading an entire multi-generation history.
            with ThreadPoolExecutor(
                max_workers=min(_STATUS_VALIDATION_WORKERS, len(accepted_ids))
            ) as executor:

                def validate_history(pv_id: str) -> dict[str, Any]:
                    if pv_id == pointer.accepted_pv:
                        return self.sessions.accepted_entry_validation(
                            project_id,
                            pv_id,
                        )
                    artifact = self.store.accepted_path(project_id, pv_id)
                    if artifact.is_file():
                        return self.store.validate_accepted(
                            project_id,
                            pv_id,
                            require_promotable=False,
                        )
                    return validate_pv_package(
                        artifact,
                        require_promotable=False,
                    )

                accepted_validations = list(
                    executor.map(validate_history, accepted_ids)
                )
        accepted_history: list[dict[str, Any]] = []
        for pv_id, validation in zip(
            accepted_ids,
            accepted_validations,
            strict=True,
        ):
            is_current = pointer.accepted_pv == pv_id
            # Current topology rules qualify the active authority only. Older
            # accepted PVs remain immutable, checksum-validated evidence even
            # when their topology predates the current promotability contract.
            lane_validation = validation["lanes"]
            accepted_history.append(
                {
                    "pv_id": pv_id,
                    "manifest_sha256": validation["manifest_sha256"],
                    "package_sha256": validation["package_sha256"],
                    "current": is_current,
                    "validation_scope": (
                        "ACCEPTED_IMMUTABLE_AUTHORITY"
                        if is_current
                        else "HISTORICAL_EVIDENCE"
                    ),
                    "integrity_validated": True,
                    "accepted_artifact_available": validation.get(
                        "accepted_artifact_available"
                    ),
                    "accepted_archive_queried": bool(
                        validation.get("accepted_archive_queried", True)
                    ),
                    "promotability_required": False,
                    "promotability_enforced": False,
                    "promotable": validation["promotable"],
                    "promotable_under_current_rules": validation["promotable"],
                    "lane_topology_status": lane_validation["status"],
                    "lane_topology_valid": lane_validation["valid"],
                    "historical_compatibility_path": bool(not validation["promotable"]),
                    "successor_candidate_must_pass_current_rules": True,
                }
            )
        current_freshness: dict[str, Any] = {
            "state": "NO_ACCEPTED_PV",
            "reason": "PV1 has not been accepted for this project.",
        }
        if pointer.accepted_pv:
            working_manifest = (
                self.store.project_root(project_id) / "sectors" / "manifest.json"
            )
            if external_project_authority and working_manifest.is_file():
                current_freshness = evaluate_working_lane_freshness(
                    self.store,
                    project_id,
                    self.store.project_root(project_id) / "sectors",
                )
            elif external_project_authority:
                current_freshness = {
                    "state": "UNAVAILABLE_WORKING_SECTORS",
                    "reason": (
                        "The external live authority has no working-sector Git "
                        "binding available for freshness comparison."
                    ),
                    "authority": "ACCEPTED_POINTER_REFERENCE_ONLY",
                }
            else:
                accepted_artifact = self.store.accepted_path(
                    project_id, pointer.accepted_pv
                )
                current_freshness = evaluate_freshness(
                    self.store, project_id, accepted_artifact
                )
        active_session: dict[str, Any] | None = None
        active_path = self.store.project_root(project_id) / "active_session.json"
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            session = self.sessions.load(project_id, active["session_id"])
            if not session.metadata.get("closed_at"):
                active_session = {
                    "session_id": session.session_id,
                    "session_snapshot_sha256": (
                        self.sessions.session_snapshot_sha256(session)
                    ),
                    "state": session.state.value,
                    "entry_pv": session.metadata.get("entry_pv"),
                    "accepted_pv": session.accepted_pv,
                    "pointer_generation": session.accepted_pointer_generation,
                    "candidate_id": session.candidate_id,
                    "pending_hil": session.state.value.endswith("_CANDIDATE"),
                    "task_id": (session.task.get("task_id") if session.task else None),
                    "backlog_task_id": session.metadata.get("active_backlog_task_id"),
                    "source_state": session.metadata.get("source_state"),
                    "host": session.host.value,
                    "persistence_route": session.metadata.get("persistence_route"),
                }
        storage_selection = self.storage_selection.inspect(project_id)
        project_route = {
            **self.store.inspect_project_route(project_id),
            "storage_mode": storage_selection["mode"],
            "storage_connector_id": storage_selection.get("connector_id"),
            "google_drive_primary_runtime_allowed": False,
        }
        if active_session:
            active_route = active_session.get("persistence_route") or {}
            project_route["active_host_profile"] = active_route.get("host_profile")
            project_route["active_server_filesystem"] = active_route.get(
                "server_filesystem"
            )
        agent_configuration = self._active_agent_configuration_authority(project_id)
        conversation_memory = self._active_conversation_memory_authority(project_id)
        lane_projection = _lane_projection(
            self.store,
            project_id,
            pointer.accepted_pv,
        )
        result.update(
            {
                "status": "PASS",
                "store": str(self.store.root),
                "project_route": project_route,
                "storage_selection": storage_selection,
                "lane_projection": lane_projection,
                "accepted_history": accepted_history,
                "current_freshness": current_freshness,
                "active_session": active_session,
                "agent_configuration": agent_configuration,
                "conversation_memory": conversation_memory,
                "persistent_state_envelope": {
                    "accepted_pv": pointer.accepted_pv,
                    "pointer_generation": pointer.generation,
                    "highest_accepted_ordinal": result["highest_accepted_ordinal"],
                    "next_candidate_pv": result["next_candidate_pv"],
                    "accepted_manifest_sha256": (pointer.accepted_manifest_sha256),
                    "pointer_snapshot_sha256": sha256_bytes(
                        canonical_json_bytes(pointer.as_dict())
                    ),
                    "freshness": current_freshness,
                    "pending_candidate": (
                        active_session["candidate_id"] if active_session else None
                    ),
                    "pending_hil": (
                        bool(active_session["pending_hil"]) if active_session else False
                    ),
                },
            }
        )
        return result

    def status_window(self, project_id: str) -> dict[str, Any]:
        """Return a bounded model-facing status projection.

        The lifecycle engine and governed UI may use :meth:`status` when they
        need complete local verification.  Public model routes receive counts,
        the current pointer, the active session boundary, and current accepted
        package facts only; accepted history, candidate IDs, lane rows, and the
        Plan ledger stay behind exact query routes.
        """

        base = self.store.project_status(project_id)
        pointer = self.store.pointer(project_id)
        external_project_authority = self.store.uses_external_project_authority(
            project_id
        )
        accepted_ids = cast(list[str], base.get("accepted") or [])
        candidate_ids = cast(list[str], base.get("candidates") or [])
        current_validation: dict[str, Any] | None = None
        if pointer.accepted_pv:
            validation = self.sessions.accepted_entry_validation(
                project_id,
                pointer.accepted_pv,
            )
            current_validation = {
                "pv_id": pointer.accepted_pv,
                "manifest_sha256": validation["manifest_sha256"],
                "package_sha256": validation["package_sha256"],
                "integrity_validated": True,
                "accepted_artifact_available": validation.get(
                    "accepted_artifact_available"
                ),
                "accepted_archive_queried": bool(
                    validation.get("accepted_archive_queried", True)
                ),
                "accepted_artifact_integrity_validated": bool(
                    validation.get("accepted_artifact_integrity_validated", True)
                ),
                "continuity_reference_integrity_validated": bool(
                    validation.get("continuity_reference_integrity_validated", False)
                ),
                "validation_scope": validation.get(
                    "validation_scope", "CURRENT_ACCEPTED_ARTIFACT"
                ),
                "promotability_required": False,
                "promotable_under_current_rules": validation["promotable"],
                "lane_topology_status": validation["lanes"]["status"],
            }

        current_freshness: dict[str, Any] = {
            "state": "NO_ACCEPTED_PV",
            "reason": "PV1 has not been accepted for this project.",
        }
        if pointer.accepted_pv:
            working_manifest = (
                self.store.project_root(project_id) / "sectors" / "manifest.json"
            )
            if external_project_authority and working_manifest.is_file():
                current_freshness = evaluate_working_lane_freshness(
                    self.store,
                    project_id,
                    self.store.project_root(project_id) / "sectors",
                    bounded_dirty_read=True,
                )
            elif external_project_authority:
                current_freshness = {
                    "state": "UNAVAILABLE_WORKING_SECTORS",
                    "reason": (
                        "The external live authority has no working-sector Git "
                        "binding available for freshness comparison."
                    ),
                    "authority": "ACCEPTED_POINTER_REFERENCE_ONLY",
                }
            else:
                accepted_artifact = self.store.accepted_path(
                    project_id, pointer.accepted_pv
                )
                current_freshness = evaluate_freshness(
                    self.store,
                    project_id,
                    accepted_artifact,
                    bounded_dirty_read=True,
                )

        active_session: dict[str, Any] | None = None
        active_path = self.store.project_root(project_id) / "active_session.json"
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            session = self.sessions.load(project_id, active["session_id"])
            if not session.metadata.get("closed_at"):
                active_session = {
                    "session_id": session.session_id,
                    "session_snapshot_sha256": (
                        self.sessions.session_snapshot_sha256(session)
                    ),
                    "state": session.state.value,
                    "entry_pv": session.metadata.get("entry_pv"),
                    "accepted_pv": session.accepted_pv,
                    "pointer_generation": session.accepted_pointer_generation,
                    "pending_candidate": session.candidate_id,
                    "pending_hil": session.state.value.endswith("_CANDIDATE"),
                    "task_id": session.task.get("task_id") if session.task else None,
                    "backlog_task_id": session.metadata.get("active_backlog_task_id"),
                    "source_state": session.metadata.get("source_state"),
                    "host": session.host.value,
                }

        storage_selection = self.storage_selection.inspect(project_id)
        project_route = {
            **self.store.inspect_project_route(project_id),
            "storage_mode": storage_selection["mode"],
            "storage_connector_id": storage_selection.get("connector_id"),
            "google_drive_primary_runtime_allowed": False,
        }
        lane_projection = _lane_projection(
            self.store,
            project_id,
            pointer.accepted_pv,
        )
        lane_window = {
            key: value
            for key, value in lane_projection.items()
            if key not in {"lanes", "absent_lane_ids"}
        }
        lane_window["absent_lane_count"] = len(
            cast(list[Any], lane_projection.get("absent_lane_ids") or [])
        )
        agent_configuration = self._active_agent_configuration_authority(project_id)
        conversation_memory = self._active_conversation_memory_authority(project_id)

        return {
            "schema": "evidence-lane.pv-status-window.v1",
            "status": "PASS",
            "project": base["project"],
            "pointer": pointer.as_dict(),
            "accepted_summary": {
                "count": len(accepted_ids),
                "highest_accepted_ordinal": base["highest_accepted_ordinal"],
                "current": current_validation,
            },
            "candidate_summary": {
                "preserved_count": len(candidate_ids),
                "next_candidate_pv": base["next_candidate_pv"],
                "active_candidate": (
                    active_session["pending_candidate"] if active_session else None
                ),
            },
            "task_backlog": base["task_backlog"],
            "project_route": project_route,
            "storage_selection": storage_selection,
            "lane_projection": lane_window,
            "current_freshness": current_freshness,
            "active_session": active_session,
            "agent_configuration": agent_configuration,
            "conversation_memory": conversation_memory,
            "persistent_state_envelope": {
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "highest_accepted_ordinal": base["highest_accepted_ordinal"],
                "next_candidate_pv": base["next_candidate_pv"],
                "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
                "pointer_snapshot_sha256": sha256_bytes(
                    canonical_json_bytes(pointer.as_dict())
                ),
                "freshness_state": current_freshness["state"],
                "pending_candidate": (
                    active_session["pending_candidate"] if active_session else None
                ),
                "pending_hil": (
                    bool(active_session["pending_hil"]) if active_session else False
                ),
            },
            "model_context_boundary": {
                "accepted_history_returned": False,
                "candidate_id_list_returned": False,
                "lane_rows_returned": False,
                "full_plan_ledger_returned": False,
                "full_pv_payload_loaded": False,
                "detail_routes": [
                    "pv_summary",
                    "pv_query",
                    "search",
                    "fetch",
                    "pv_task_backlog",
                ],
            },
        }

    def plan_tasks(
        self,
        project_id: str,
        *,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str | None = None,
        host_kind: str | None = None,
        host_mode: str | None = None,
        normalization_transition: dict[str, Any] | None = None,
        atomic_insertion: dict[str, Any] | None = None,
        active_contract_rebind: dict[str, Any] | None = None,
        existing_task_promotion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        exact_host = str(host_kind or "").strip().upper()
        exact_mode = str(host_mode or "").strip().upper()
        if exact_host and not exact_host.startswith("CODEX"):
            return {
                "status": "HOST_UNSUPPORTED",
                "plan_persisted": False,
                "host_kind": exact_host,
                "host_mode": exact_mode or "NOT_DECLARED",
                "canonical_authority": "PLAN_LANE",
                "message": (
                    "The v2 Plan bridge accepts Codex hosts only and cannot add "
                    "rows for this host to the Codex Goal projection."
                ),
            }
        if exact_host.startswith("CODEX") and exact_mode != "PLAN":
            return {
                "status": "PLAN_MODE_REQUIRED",
                "plan_persisted": False,
                "host_kind": exact_host,
                "host_mode": exact_mode or "NOT_DECLARED",
                "suggested_next_prompt": "/pl",
                "message": (
                    "Turn on Codex Plan mode with /pl, finish the plan, then run "
                    "$evi-plan so Plan Lane and the native Goal/task panel pair."
                ),
            }
        exact_plan_id = plan_id or prefixed_id("plan")
        write_modes = (
            normalization_transition,
            atomic_insertion,
            active_contract_rebind,
            existing_task_promotion,
        )
        require(
            sum(mode is not None for mode in write_modes) <= 1,
            "PLAN_TASKS_WRITE_MODE_CONFLICT",
            "Plan normalization, atomic insertion, active-contract rebind, and existing-task promotion are mutually exclusive.",
            status="BLOCKED",
        )
        if normalization_transition is not None:
            result = self.sessions.normalize_plan_tasks(
                project_id,
                tasks=tasks,
                planned_by=planned_by,
                plan_id=exact_plan_id,
                normalization_transition=normalization_transition,
            )
        elif atomic_insertion is not None:
            require(
                isinstance(atomic_insertion, dict) and not tasks,
                "PLAN_ATOMIC_INSERTION_CONTRACT_INVALID",
                "Atomic insertion requires one object and an empty top-level task list.",
                status="BLOCKED",
            )
            priority_interruption = atomic_insertion.get("priority_interruption")
            if priority_interruption is not None:
                require(
                    isinstance(priority_interruption, dict),
                    "PLAN_PRIORITY_STEER_CONTRACT_INVALID",
                    "The atomic priority interruption must be one object.",
                    status="BLOCKED",
                )
                result = self.sessions.apply_priority_plan_interruption(
                    project_id,
                    planned_by=planned_by,
                    plan_id=exact_plan_id,
                    atomic_insertion=atomic_insertion,
                    priority_interruption=priority_interruption,
                )
            else:
                result = self.store.plan_tasks_atomic_insert(
                    project_id,
                    insertions=cast(
                        list[dict[str, Any]], atomic_insertion.get("insertions")
                    ),
                    planned_by=planned_by,
                    plan_id=exact_plan_id,
                    batch_id=str(atomic_insertion.get("batch_id") or ""),
                    research_batch_sha256=str(
                        atomic_insertion.get("research_batch_sha256") or ""
                    ),
                    expected_backlog_sha256=str(
                        atomic_insertion.get("expected_backlog_sha256") or ""
                    ),
                    expected_canonical_plan_sha256=str(
                        atomic_insertion.get("expected_canonical_plan_sha256") or ""
                    ),
                    expected_executable_projection_sha256=str(
                        atomic_insertion.get("expected_executable_projection_sha256")
                        or ""
                    ),
                    expected_physical_final_task_id=str(
                        atomic_insertion.get("expected_physical_final_task_id") or ""
                    ),
                )
        elif active_contract_rebind is not None:
            require(
                isinstance(active_contract_rebind, dict) and not tasks,
                "ACTIVE_CONTRACT_REBIND_CONTRACT_INVALID",
                "An active-contract rebind requires one object and an empty top-level task list.",
                status="BLOCKED",
            )
            self._capture_route_binding(project_id)
            result = self.sessions.rebind_active_task_contract(
                project_id,
                rebound_by=planned_by,
                active_contract_rebind=active_contract_rebind,
            )
        elif existing_task_promotion is not None:
            require(
                isinstance(existing_task_promotion, dict) and not tasks,
                "PLAN_EXISTING_TASK_PROMOTION_CONTRACT_INVALID",
                "Existing-task promotion requires one object and an empty top-level task list.",
                status="BLOCKED",
            )
            result = self.sessions.promote_existing_plan_task(
                project_id,
                promoted_by=planned_by,
                existing_task_promotion=existing_task_promotion,
            )
        else:
            result = self.store.plan_tasks(
                project_id,
                tasks=tasks,
                planned_by=planned_by,
                plan_id=exact_plan_id,
            )
        result["host_plan_bridge"] = {
            "host_kind": exact_host or "UNDECLARED",
            "host_mode": exact_mode or "UNDECLARED",
            "canonical_authority": "PLAN_LANE",
            "codex_goal_start_prompt": result["goal_projection"]["goal_start_prompt"],
            "copy_paste_required": exact_host.startswith("CODEX"),
            "host_goal_mutation_supported_by_mcp": False,
            "host_scope": "CODEX_ONLY",
        }
        result["agent_configuration"] = self._active_agent_configuration_authority(
            project_id
        )
        result["conversation_memory"] = self._active_conversation_memory_authority(
            project_id
        )
        return result

    def record_steer_delta(
        self,
        project_id: str,
        *,
        delta_text: str,
        actor: str,
        delta_id: str,
        linked_task_id: str | None = None,
        new_task_contract: dict[str, Any] | None = None,
        boundary: str = "BEFORE_NEXT_HIL",
    ) -> dict[str, Any]:
        result = self.store.record_steer_delta(
            project_id,
            delta_text=delta_text,
            actor=actor,
            delta_id=delta_id,
            linked_task_id=linked_task_id,
            new_task_contract=new_task_contract,
            boundary=boundary,
        )
        effect = cast(dict[str, Any], result.get("host_plan_window_effect") or {})
        if effect.get("host_update_plan_required") is True:
            result["host_plan_window_rebind"] = (
                self.sessions.bind_host_plan_window_after_plan_mutation(
                    project_id,
                    window_task_ids=[
                        str(task_id)
                        for task_id in cast(
                            list[Any], effect.get("window_task_ids") or []
                        )
                    ],
                    linked_task_id=str(effect.get("linked_task_id") or ""),
                )
            )
        return result

    def task_backlog(self, project_id: str) -> dict[str, Any]:
        result = self.store.backlog_status(project_id)
        result["agent_configuration"] = self._active_agent_configuration_authority(
            project_id
        )
        result["conversation_memory"] = self._active_conversation_memory_authority(
            project_id
        )
        return result

    def task_backlog_window(
        self,
        project_id: str,
        *,
        task_id: str | None = None,
        query: str | None = None,
        limit: int = 9,
    ) -> dict[str, Any]:
        """Return a compact active window or one bounded live-Plan query."""

        exact_task_id = str(task_id or "").strip()
        exact_query = str(query or "").strip()
        if exact_task_id or exact_query:
            result = self.store.plan_runtime_query(
                project_id,
                task_id=exact_task_id or None,
                query=exact_query or None,
                limit=min(int(limit), 20),
            )
            result["agent_configuration"] = self._active_agent_configuration_authority(
                project_id
            )
            result["conversation_memory"] = self._active_conversation_memory_authority(
                project_id
            )
            return result
        require(
            int(limit) == 9,
            "TASK_BACKLOG_WINDOW_FIXED_CARDINALITY_REQUIRED",
            "The native host Plan projection uses one fixed batch of at most nine Delta rows; the compact header is separate.",
            status="BLOCKED",
            limit=limit,
        )
        backlog = self.store.backlog_status(project_id)
        goal = cast(dict[str, Any], backlog.get("goal_projection") or {})
        rows = cast(list[dict[str, Any]], goal.get("rows") or [])
        active_rows = [
            row
            for row in rows
            if row.get("status") == "in_progress"
            and row.get("lifecycle_status") == "ACTIVE"
        ]
        require(
            len(active_rows) <= 1,
            "TASK_BACKLOG_WINDOW_ACTIVE_ROW_INVALID",
            "The live Plan projection contains multiple ACTIVE rows.",
            status="MISMATCH",
            active_count=len(active_rows),
        )
        projection: dict[str, Any] | None = None
        if active_rows:
            fixed_window_task_ids = self.store.persisted_host_plan_window_task_ids(
                project_id
            )
            require(
                bool(fixed_window_task_ids),
                "HOST_PLAN_FIXED_BATCH_REQUIRED",
                "The canonical fixed host batch is missing; the obsolete sliding/de-dup projector is disabled.",
                status="BLOCKED",
            )
            projection = _exact_projection(
                self.store,
                project_id=project_id,
                fixed_window_task_ids=fixed_window_task_ids,
            )
            window_task_ids = list(projection["window_task_ids"])
        else:
            # A backlog may be inspected before Plan activation, but it is not a
            # host Step Task List and must never masquerade as its projector.
            # Goal presence is deliberately irrelevant once a Plan row is ACTIVE.
            window_task_ids = [str(row["task_id"]) for row in rows[:9]]
        rows_by_task_id = {str(row["task_id"]): row for row in rows}
        window_rows = [rows_by_task_id[str(task_id)] for task_id in window_task_ids]
        compact_rows = [
            {
                "number": int(row["number"]),
                "task_id": str(row["task_id"]),
                "status": str(row["status"]),
                "lifecycle_status": str(row["lifecycle_status"]),
                "task_classification": str(row["task_classification"]),
                "plan_group": str(row["plan_group"]),
                "commit_batch_id": str(row["commit_batch_id"]),
                "git_commit_stage": str(row["git_commit_stage"]),
                "dependencies": list(row.get("dependencies") or []),
                "panel_role": str(row.get("panel_role") or "STANDARD"),
                "exact_detail_lookup": {
                    "task_id": str(row["task_id"]),
                    "mode": "EXACT_TASK_ID_THEN_BOUNDED_FTS",
                },
            }
            for row in window_rows
        ]
        runtime = cast(dict[str, Any], backlog.get("plan_runtime_projection") or {})
        plan_runtime_receipt = {
            "schema": "evidence-lane.plan-runtime-bounded-receipt.v1",
            "status": runtime.get("status"),
            "sqlite_sha256": runtime.get("sqlite_sha256"),
            "projection_content_sha256": runtime.get("projection_content_sha256"),
            "event_count": runtime.get("event_count"),
            "steer_count": runtime.get("steer_count"),
            "execution_row_count": runtime.get("execution_row_count"),
            "history_row_count": runtime.get("history_row_count"),
            "fts_record_count": runtime.get("fts_record_count"),
            "fts5_enabled": runtime.get("fts5_enabled"),
            "detail_lookup_policy": runtime.get("detail_lookup_policy"),
            "full_runtime_projection_returned": False,
            "raw_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
        }
        return {
            "status": "PASS",
            "schema": "evidence-lane.task-backlog-window.v1",
            "project_id": project_id,
            "canonical_authority": "PLAN_LANE",
            "canonical_task_count": int(goal.get("canonical_task_count") or 0),
            "total_executable_count": len(rows),
            "history_task_count": int(goal.get("history_task_count") or 0),
            "counts": backlog.get("counts"),
            "canonical_plan_sha256": goal.get("canonical_plan_sha256"),
            "executable_projection_sha256": goal.get("projection_sha256"),
            "window_size": 9,
            "maximum_host_item_count": 10,
            "window_row_start": (
                projection["row_start"]
                if projection is not None
                else (int(window_rows[0]["number"]) if window_rows else None)
            ),
            "window_row_end": (
                projection["row_end"]
                if projection is not None
                else (int(window_rows[-1]["number"]) if window_rows else None)
            ),
            "absolute_active_row": (
                projection["sole_active_row"] if projection is not None else None
            ),
            "absolute_active_task_id": (
                projection["sole_active_task_id"] if projection is not None else None
            ),
            "rows": compact_rows,
            "items": projection["items"] if projection is not None else [],
            "row_ui_contract": (
                "HEADER_THEN_FOUR_LINES_PER_DELTA_2_AUTHORITY_2_HUMAN"
                if projection is not None
                else "NO_HOST_STEP_LIST_BEFORE_PLAN_ACTIVATION"
            ),
            "fixed_header": (
                projection["continuity_header"] if projection is not None else None
            ),
            "host_update_plan_contract": (
                projection["host_update_plan_contract"]
                if projection is not None
                else None
            ),
            "window_task_ids": window_task_ids,
            "window_ui_fingerprint_sha256": (
                projection["window_ui_fingerprint_sha256"]
                if projection is not None
                else None
            ),
            "full_ledger_returned": False,
            "full_row_reconstructed_in_model_context": False,
            "accepted_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
            "exact_row_query_available": True,
            "bounded_fts_query_available": True,
            "plan_runtime_receipt": plan_runtime_receipt,
            "agent_configuration": (
                self._active_agent_configuration_authority(project_id)
            ),
            "conversation_memory": (
                self._active_conversation_memory_authority(project_id)
            ),
        }

    def transition_task(
        self,
        project_id: str,
        *,
        task_id: str,
        transition_name: str,
        decided_by: str,
        reason: str,
        replacement_task_id: str | None = None,
        event_id: str | None = None,
        correction_of_event_id: str | None = None,
        expected_backlog_sha256: str | None = None,
    ) -> dict[str, Any]:
        exact_reason = reason.strip()
        require(
            bool(exact_reason),
            "DELTA_TRANSITION_REASON_REQUIRED",
            "DROP and SUPERSEDE require one visible reason.",
            status="BLOCKED",
        )
        return self.store.transition_backlog_task(
            project_id,
            task_id=task_id,
            transition_name=transition_name,
            decided_by=decided_by,
            reason_sha256=sha256_bytes(exact_reason.encode("utf-8")),
            replacement_task_id=replacement_task_id,
            event_id=event_id,
            correction_of_event_id=correction_of_event_id,
            expected_backlog_sha256=expected_backlog_sha256,
        )

    def register_project(
        self,
        *,
        project_id: str,
        display_name: str,
        repository_path: str,
        expected_owner: str,
        expected_name: str,
        allowed_branches: list[str],
        sensitivity: str = "PRIVATE",
        capture_route: str = "GOVERNED_PROJECT_FULL",
        project_authority_root: str | None = None,
        project_authority_migration_confirmation: str | None = None,
        expected_accepted_pv: str | None = None,
        expected_pointer_generation: int | None = None,
        selected_by: str | None = None,
    ) -> dict[str, Any]:
        resolved_repository = str(Path(repository_path).resolve())
        resolved_authority_root = (
            str(Path(project_authority_root).resolve())
            if project_authority_root
            else None
        )
        existing: ProjectConfig | None = None
        try:
            existing = self.store.config(project_id)
        except EvidenceLaneError as exc:
            if exc.code != "PROJECT_NOT_REGISTERED":
                raise
        if existing is not None and resolved_authority_root is not None:
            expected_fields = {
                "display_name": display_name,
                "repository_path": resolved_repository,
                "expected_owner": expected_owner,
                "expected_name": expected_name,
                "allowed_branches": allowed_branches,
                "sensitivity": sensitivity.upper(),
                "capture_route": capture_route,
            }
            mismatches = {
                key: {
                    "registered": getattr(existing, key),
                    "requested": value,
                }
                for key, value in expected_fields.items()
                if getattr(existing, key) != value
            }
            require(
                not mismatches,
                "PROJECT_AUTHORITY_MIGRATION_REGISTRATION_MISMATCH",
                "Project authority relocation cannot alter the registered source or project identity.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            route = self.store.inspect_project_route(project_id)
            if route["resolved_project_root"] == resolved_authority_root:
                return {
                    "status": "PASS",
                    "state": "REGISTERED_EXTERNAL_AUTHORITY_IDEMPOTENT_REUSE",
                    **self.store.project_authority_status(project_id),
                }
            require(
                project_authority_migration_confirmation is not None
                and expected_accepted_pv is not None
                and expected_pointer_generation is not None
                and selected_by is not None,
                "PROJECT_AUTHORITY_MIGRATION_PRECONDITION_REQUIRED",
                "Relocating an existing project requires confirmation, actor, accepted PV, and pointer generation.",
                status="BLOCKED",
            )
            migration = self.migrate_project_authority(
                project_id,
                target_root=resolved_authority_root,
                selected_by=cast(str, selected_by),
                confirmation=cast(str, project_authority_migration_confirmation),
                expected_accepted_pv=cast(str, expected_accepted_pv),
                expected_pointer_generation=cast(int, expected_pointer_generation),
            )
            return {
                "status": "PASS",
                "state": "REGISTERED_PROJECT_AUTHORITY_RELOCATED",
                "migration": migration,
                "project_authority": self.store.project_authority_status(project_id),
            }
        return {
            "status": "PASS",
            **self.store.register_project(
                ProjectConfig(
                    project_id=project_id,
                    display_name=display_name,
                    repository_path=resolved_repository,
                    expected_owner=expected_owner,
                    expected_name=expected_name,
                    allowed_branches=allowed_branches,
                    source_lane="local_code",
                    persistence_mode="governed_by_host",
                    sensitivity=sensitivity.upper(),
                    capture_route=capture_route,
                    project_authority_root=resolved_authority_root,
                )
            ),
        }

    def project_authority_status(self, project_id: str) -> dict[str, Any]:
        return self.store.project_authority_status(project_id)

    def project_pv_storage_status(self, project_id: str) -> dict[str, Any]:
        return self.store.project_pv_storage_status(project_id)

    def migrate_project_authority(
        self,
        project_id: str,
        *,
        target_root: str,
        selected_by: str,
        confirmation: str,
        expected_accepted_pv: str,
        expected_pointer_generation: int,
    ) -> dict[str, Any]:
        return self.store.migrate_project_authority(
            project_id,
            target_root=target_root,
            selected_by=selected_by,
            confirmation=confirmation,
            expected_accepted_pv=expected_accepted_pv,
            expected_pointer_generation=expected_pointer_generation,
        )

    def enroll_project(
        self,
        *,
        project_id: str,
        display_name: str,
        source: str,
        expected_owner: str,
        expected_name: str,
        branch: str,
        sensitivity: str = "PRIVATE",
        capture_route: str = "GOVERNED_PROJECT_FULL",
    ) -> dict[str, Any]:
        return enroll_project(
            self.store,
            project_id=project_id,
            display_name=display_name,
            source=source,
            expected_owner=expected_owner,
            expected_name=expected_name,
            branch=branch,
            sensitivity=sensitivity,
            capture_route=capture_route,
        )

    def sync_git_source(
        self,
        *,
        project_id: str,
        source: str,
        branch: str,
        session_id: str | None = None,
        expected_commit: str | None = None,
        replace_registered_branch: bool = False,
    ) -> dict[str, Any]:
        active_path = self.store.project_root(project_id) / "active_session.json"
        session = None
        permitted_paths = None
        interrupted_exit_authority_recovery = False
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            require(
                bool(session_id) and active.get("session_id") == session_id,
                "PROJECT_SYNC_ACTIVE_SESSION_REQUIRED",
                "An active governed session exists; Git sync requires its exact "
                "session ID and bounded task contract.",
                status="BLOCKED",
                active_session_id=active.get("session_id"),
            )
            session = self.sessions.load(project_id, str(session_id))
            interrupted_exit_authority_recovery = (
                replace_registered_branch
                and session.state.value == "EXIT_BUILDING"
                and session.task is not None
                and session.candidate_id is None
            )
            require(
                session.task is not None
                and session.state.value
                in {"TASK_CLASSIFIED", "AWAITING_USER_APPLY_COMMIT"}
                or interrupted_exit_authority_recovery,
                "PROJECT_SYNC_TASK_STATE_INVALID",
                "Git sync inside an active session requires one classified task, "
                "or an explicit candidate-free interrupted-exit branch-authority "
                "recovery.",
                status="BLOCKED",
                state=session.state.value,
                candidate_id=session.candidate_id,
            )
            task_payload = cast(dict[str, Any], session.task)
            permitted_paths = list(task_payload["permitted_paths"])
        result = sync_selected_branch(
            self.store,
            project_id=project_id,
            source=source,
            branch=branch,
            expected_commit=expected_commit,
            permitted_paths=permitted_paths,
            branch_replacement_actor=(
                session.user_id
                if replace_registered_branch and session is not None
                else None
            ),
            dirty_local_authority_context=(
                {
                    "session_id": session.session_id,
                    "task_id": str(task_payload["task_id"]),
                    "task_class": str(task_payload["task_class"]),
                    "lifecycle_state": session.state.value,
                    "task_contract_sha256": sha256_bytes(
                        canonical_json_bytes(task_payload)
                    ),
                }
                if replace_registered_branch
                and session is not None
                and permitted_paths is not None
                else None
            ),
        )
        if session is not None and not interrupted_exit_authority_recovery:
            activity = self.sessions.record_activity(
                project_id,
                session.session_id,
                activity_type=(
                    "git.fast_forward"
                    if result["fast_forward_applied"]
                    else "git.fast_forward.noop"
                ),
                visible_payload={
                    "branch": result["branch"],
                    "source_kind": result["source_kind"],
                    "before_commit": result["before"]["commit_sha"],
                    "after_commit": result["after"]["commit_sha"],
                    "changed_paths": result["changed_paths"],
                    "branch_authority": result["branch_authority"],
                    "operation": result.get("operation"),
                    "dirty_worktree_preserved": result.get(
                        "dirty_worktree_preserved", False
                    ),
                    "worktree_status_sha256": result.get("worktree_status_sha256"),
                    "fetch_performed": result.get("fetch_performed", True),
                    "source_write_performed": result.get(
                        "source_write_performed", False
                    ),
                    "remote_write_performed": False,
                    "merge_commit_created": False,
                },
            )
            result["activity"] = activity["event"]
        elif interrupted_exit_authority_recovery:
            result["activity_recorded"] = False
            result["activity_deferred_reason"] = (
                "CANDIDATE_FREE_INTERRUPTED_EXIT_AUTHORITY_RECOVERY"
            )
        return result

    def boot_session(
        self,
        *,
        project_id: str,
        user_id: str,
        workspace_id: str,
        host: str,
        agent_id: str,
        sandbox_id: str | None,
        ephemeral: bool,
        runtime_context: dict[str, Any] | None,
        host_session_id: str | None = None,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        capture_route_binding = self._capture_route_binding(project_id)
        project_lineage_entry = ProjectChatLineage(
            resolved_chat_lineage_root(self.store.project_root(project_id))
        ).sync()
        flash = self.flash_authority.ensure_flashed()
        host_kind = normalize_host_kind(host)
        route, storage_selection = self._selected_persistence_route(
            project_id,
            host=host_kind.value,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            host_session_id=host_session_id,
        )
        if route.durable_required and (
            self.sync_service is None or not self.sync_service.runtime_state_capable
        ):
            raise EvidenceLaneError(
                "DURABLE_RUNTIME_CONNECTOR_NOT_CONFIGURED",
                "This remote or ephemeral host requires a transactional connector for "
                "sessions, backlog, lineage, candidates, receipts, and pointer CAS. "
                "Google Drive may mirror sealed artifacts but is never this primary authority.",
                status="BLOCKED",
                details={"mode": route.mode, "reason": route.reason},
            )
        if route.server_filesystem != "DURABLE":
            raise EvidenceLaneError(
                "HOST_ENTRY_REMOTE_BOOT_REQUIRES_EXACT_SESSION_RECOVERY",
                "A new insufficiently durable Boot cannot invent a governed session; recover the exact durable session and consume its host-entry envelope through Resume.",
                status="BLOCKED",
            )
        route_payload = self._persistence_route_payload(project_id, route)
        result = self.sessions.boot(
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=host_kind,
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            persistence_mode=route.mode,
            persistence_route=route_payload,
            ephemeral=ephemeral,
            runtime_context=runtime_context,
            flash=flash,
            host_session_id=host_session_id,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=route.server_filesystem == "DURABLE",
        )
        result["persistence_route"] = {
            **route_payload,
            "selection": storage_selection,
        }
        result["project_lineage_entry"] = project_lineage_entry
        result["capture_route_binding"] = capture_route_binding
        result["project_lineage"] = ProjectChatLineage(
            resolved_chat_lineage_root(self.store.project_root(project_id))
        ).sync()
        result["agent_configuration"] = self._active_agent_configuration_authority(
            project_id
        )
        result["conversation_memory"] = self._active_conversation_memory_authority(
            project_id
        )
        return result

    def resume_session(
        self,
        *,
        project_id: str,
        host: str,
        host_session_id: str,
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        capture_route_binding = self._capture_route_binding(project_id)
        project_lineage_entry = ProjectChatLineage(
            resolved_chat_lineage_root(self.store.project_root(project_id))
        ).sync()
        flash = self.flash_authority.ensure_flashed()
        host_kind = normalize_host_kind(host)
        route, storage_selection = self._selected_persistence_route(
            project_id,
            host=host_kind.value,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            host_session_id=host_session_id,
        )
        if route.durable_required and (
            self.sync_service is None or not self.sync_service.runtime_state_capable
        ):
            raise EvidenceLaneError(
                "DURABLE_RUNTIME_CONNECTOR_NOT_CONFIGURED",
                "This MCP server has no durable filesystem and requires the direct "
                "transactional runtime connector before resume; Drive remains a mirror.",
                status="BLOCKED",
                details={"mode": route.mode, "reason": route.reason},
            )
        route_payload = self._persistence_route_payload(project_id, route)
        host_entry_consumption = self._consume_remote_host_entry_for_resume(
            project_id,
            route=route,
            host_session_id=host_session_id,
            runtime_context=runtime_context,
            project_lineage_entry=project_lineage_entry,
            flash=flash,
        )
        result = self.sessions.resume(
            project_id=project_id,
            host=host_kind,
            host_session_id=host_session_id,
            persistence_mode=route.mode,
            persistence_route=route_payload,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=route.server_filesystem == "DURABLE",
            runtime_context=runtime_context,
            flash=flash,
            host_entry_consumption=host_entry_consumption,
        )
        result["session_flash"] = flash
        result["persistence_route"] = {
            **route_payload,
            "selection": storage_selection,
        }
        result["project_lineage_entry"] = project_lineage_entry
        result["capture_route_binding"] = capture_route_binding
        result["project_lineage"] = ProjectChatLineage(
            resolved_chat_lineage_root(self.store.project_root(project_id))
        ).sync()
        result["agent_configuration"] = self._active_agent_configuration_authority(
            project_id
        )
        result["conversation_memory"] = self._active_conversation_memory_authority(
            project_id
        )
        return result

    def direct_force_same_worktree_state_travel(
        self,
        *,
        project_id: str,
        session_id: str,
        authoritative_source_task_id: str,
        runtime_attachment_donor_task_id: str,
        destination_task_id: str,
        destination_task_title: str,
    ) -> dict[str, Any]:
        """Atomically derive and bind one fresh same-worktree destination.

        This is the only public direct-entry service route. Callers provide
        task identities, never a nonce or volatile source/Plan/runtime fields.
        """

        capture_route_binding = self._capture_route_binding(project_id)
        flash = self.flash_authority.ensure_flashed()
        with self.store.state_travel_resume_lock(project_id):
            session = self.sessions.load(project_id, session_id)
            profile_value = session.metadata.get("execution_profile")
            require(
                isinstance(profile_value, dict),
                "DIRECT_STATE_TRAVEL_EXECUTION_PROFILE_REQUIRED",
                "The active governed session has no exact execution profile.",
                status="MISMATCH",
                writes_performed=False,
            )
            execution_profile = cast(dict[str, Any], profile_value)
            route, storage_selection = self._selected_persistence_route(
                project_id,
                host="CODEX_DESKTOP",
                ephemeral=False,
                server_has_durable_filesystem=True,
                runtime_context=execution_profile,
                host_session_id=destination_task_id,
            )
            require(
                route.server_filesystem == "DURABLE" and not route.durable_required,
                "DIRECT_STATE_TRAVEL_DURABLE_LOCAL_AUTHORITY_REQUIRED",
                "Direct same-worktree State Travel requires the existing durable local authority.",
                status="BLOCKED",
                writes_performed=False,
            )
            route_payload = self._persistence_route_payload(project_id, route)
            binding = self.sessions._server_derived_direct_same_worktree_binding(
                project_id,
                session_id,
                authoritative_source_task_id=authoritative_source_task_id,
                runtime_attachment_donor_task_id=(runtime_attachment_donor_task_id),
                destination_task_id=destination_task_id,
                destination_task_title=destination_task_title,
            )
            preflight = preflight_direct_forced_same_worktree_binding(binding)
            exact_binding = cast(dict[str, Any], preflight["normalized_binding"])
            result = self.sessions.direct_force_same_worktree_entry(
                project_id,
                session_id,
                binding=exact_binding,
                persistence_mode=route.mode,
                persistence_route=route_payload,
                flash=flash,
                client_can_edit_source=True,
                server_has_durable_filesystem=True,
            )

        direct = cast(dict[str, Any], result["direct_state_travel"])
        plan = cast(dict[str, Any], direct["plan_task_proof"])
        pointer = cast(dict[str, Any], direct["accepted_pointer_baseline"])
        task_binding = cast(dict[str, Any], direct["calling_task_binding_authority"])
        orchestration = cast(dict[str, Any], direct["destination_orchestration"])
        receipt_body = {
            "schema": (
                "evidence-lane.server-derived-forced-same-worktree-entry-receipt.v1"
            ),
            "status": "PASS",
            "route": "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK",
            "binding_mode": "SERVER_DERIVED_ATOMIC",
            "project_id": project_id,
            "session_id": session_id,
            "task_binding": {
                "authoritative_source_task_id": authoritative_source_task_id,
                "runtime_attachment_donor_task_id": (runtime_attachment_donor_task_id),
                "destination_task_id": destination_task_id,
                "destination_task_deep_link": (
                    f"codex://threads/{destination_task_id}"
                ),
                "destination_task_title": destination_task_title,
            },
            "accepted_baseline": {
                "pv_id": pointer["accepted_pv"],
                "generation": pointer["generation"],
                "moved": False,
            },
            "plan_anchors": {
                "active_row": plan["active_row"],
                "active_task_id": plan["active_task_id"],
                "fixed_batch_row_start": plan["active_batch_row_start"],
                "fixed_batch_row_end": plan["active_batch_row_end"],
                "next_hil_row": plan["next_hil_row"],
                "physically_final_hil_row": plan["physically_final_hil_row"],
            },
            "runtime_attestation_receipt_sha256": task_binding[
                "runtime_instance_attestation_receipt_sha256"
            ],
            "direct_entry_receipt_sha256": direct["receipt_sha256"],
            "destination_orchestration_receipt_sha256": orchestration["receipt_sha256"],
            "server_minted_replay_guard": True,
            "caller_supplied_nonce": False,
            "caller_supplied_volatile_authority": False,
            "sealed_prepare_called": False,
            "sealed_resume_called": False,
            "source_mutated": False,
            "candidate_created": False,
            "hil_inferred": False,
            "pointer_moved": False,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        return {
            "status": "PASS",
            "forced_state_travel": receipt,
            "destination_orchestration": orchestration,
            "runtime_activation": result["runtime_activation"],
            "session_flash": flash,
            "persistence_route": {
                **route_payload,
                "selection": storage_selection,
            },
            "capture_route_binding": capture_route_binding,
            "agent_configuration": self._active_agent_configuration_authority(
                project_id
            ),
            "conversation_memory": self._active_conversation_memory_authority(
                project_id
            ),
        }

    def build_initial(self, project_id: str, session_id: str) -> dict[str, Any]:
        if self.store.uses_external_project_authority(project_id):
            plan = self.store.backlog_status(project_id)
            require(
                int(
                    dict(plan.get("canonical_plan_projection") or {}).get(
                        "task_count"
                    )
                    or 0
                )
                > 0
                and dict(plan.get("plan_runtime_projection") or {}).get("status")
                == "PASS",
                "PV0_INITIAL_PLAN_REQUIRED",
                "PV0 work starts only after EVI Plan has persisted the initial canonical Plan.",
                status="BLOCKED",
                project_id=project_id,
            )
            config = self.store.config(project_id)
            source_intake = self.source_intake(
                project_id,
                [config.repository_path],
                session_id=session_id,
                git_mode="AUTO",
                authority_mode="GOVERNED_CONTENT_REGISTRY",
                working_authority_action="REFRESH_WORKING_SECTORS",
            )
            result = self.sessions.bootstrap_pv0_entry(
                project_id,
                session_id,
                source_intake=source_intake,
            )
            result["next_action"] = "CONTINUE_ACTIVE_GOAL_AND_STEP_TASK_LIST"
            result["next_action_contract"] = {
                "schema": "evidence-lane.initial-plan-goal-pv0-handoff.v2",
                "ordered_actions": [
                    "PROJECT_REGISTERED_WITH_EXTERNAL_AUTHORITY",
                    "EVI_PLAN_PERSISTED_BEFORE_PV0",
                    "HOST_PLAN_EXPLICITLY_ACCEPTED",
                    "GOAL_AND_STEP_TASK_LIST_BOUND_BY_HOST_HOOKS",
                    "SOURCE_INTAKE_AND_BUILD_ESTABLISHED_PV0",
                    "CONTINUE_ACTIVE_PLAN_ROW",
                ],
                "evi_plan_completed_before_pv0": True,
                "host_plan_acceptance_required": True,
                "goal_start_requires_plan_acceptance": True,
                "goal_and_step_binding_precede_source_work": True,
                "state_travel_registration_or_pv0_creation_allowed": False,
                "delta_exit_not_invoked": True,
                "hil_not_invoked": True,
                "pointer_moved": False,
            }
            result["hil_choices"] = []
            result["suggested_next_prompt"] = (
                "Continue the active Goal and fixed Step Task List from the initial "
                "Plan. PV0 is established without HIL; proceed with the active row."
            )
            result["agent_configuration"] = (
                self._active_agent_configuration_authority(project_id)
            )
            result["conversation_memory"] = (
                self._active_conversation_memory_authority(project_id)
            )
            return result
        result = self.sessions.build_initial_entry(project_id, session_id)
        result["next_action"] = "PRESENT_SIX_WAY_HIL"
        result["suggested_next_prompt"] = HIL_SUGGESTED_PROMPT
        result["next_action_contract"] = result["candidate"]["next_action"]
        result["hil_choices"] = list(HIL_CHOICES)
        result["agent_configuration"] = self._active_agent_configuration_authority(
            project_id
        )
        result["conversation_memory"] = self._active_conversation_memory_authority(
            project_id
        )
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["durable_persistence"] = sync.sync_pv(
                project_id,
                result["candidate"]["candidate_id"],
                category="candidates",
            )
        return result

    def _refresh_hil_candidate(
        self,
        project_id: str,
        session_id: str,
        *,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        """Seal the HIL-only live-root proposal through its current private owner."""

        result = self.sessions.refresh_exit(
            project_id,
            session_id,
            batch_task_evidence=batch_task_evidence,
            batch_completion_confirmation=batch_completion_confirmation,
        )
        result["next_action"] = "PRESENT_SIX_WAY_HIL"
        result["suggested_next_prompt"] = HIL_SUGGESTED_PROMPT
        result["next_action_contract"] = result["candidate"]["next_action"]
        result["hil_choices"] = list(HIL_CHOICES)
        if self.store.uses_external_project_authority(project_id):
            result["dual_hil_presentation"] = self._dual_hil_presentation(
                project_id,
                candidate=result["candidate"],
            )
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["durable_persistence"] = sync.sync_pv(
                project_id,
                result["candidate"]["candidate_id"],
                category="candidates",
            )
        return result

    def _dual_hil_presentation(
        self,
        project_id: str,
        *,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the conjoined Project/Learning HIL summary without archive reads."""

        proposed_pv = str(candidate.get("proposed_pv") or "").strip().upper()
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        learning = inspect_learning_authority(
            self.store.project_root(project_id),
            project_id=project_id,
        )
        matching = [
            dict(row)
            for row in list(learning.get("learning_weaves") or [])
            if row.get("target_project_pv") == proposed_pv
            and row.get("weave_candidate_state")
            in {"PENDING_LEARNING_HIL", "ACCEPTED"}
        ]
        require(
            bool(proposed_pv)
            and bool(candidate_id)
            and len(matching) == 1,
            "DUAL_HIL_LEARNING_WEAVE_REQUIRED",
            "A full-PV Project proposal must present exactly one consolidated "
            "Learning weave for the same PV before either HIL decision.",
            status="BLOCKED",
            proposed_pv=proposed_pv or None,
            matching_weave_count=len(matching),
        )
        weave = matching[0]
        member_count = int(weave.get("member_count") or 0)
        ledger_candidate_count = int(learning.get("candidate_count") or 0)
        relevant_hil_candidate_count = member_count + 1
        return {
            "schema": "evidence-lane.dual-project-learning-hil.v1",
            "status": "PASS",
            "project_id": project_id,
            "target_pv": proposed_pv,
            "project": {
                "proposal_id": candidate_id,
                "proposal_manifest_sha256": candidate.get("manifest_sha256"),
                "decision_state": "PENDING_PROJECT_HIL",
                "summary": "LIVE_ROOT_PROPOSAL_PLUS_HIL_ONLY_PROJECT_OVERLAY",
            },
            "learning": {
                "weave_candidate_id": weave["weave_candidate_id"],
                "weave_candidate_sha256": weave["weave_candidate_sha256"],
                "weave_receipt_sha256": weave["receipt_sha256"],
                "decision_state": weave["weave_candidate_state"],
                "auto_accepted_delta_member_count": member_count,
                "hil_relevant_candidate_count": relevant_hil_candidate_count,
                "ledger_candidate_count": ledger_candidate_count,
                "immutable_other_history_count": max(
                    0, ledger_candidate_count - relevant_hil_candidate_count
                ),
                "member_set_sha256": weave["member_set_sha256"],
                "full_member_payload_returned": False,
                "summary": (
                    f"{member_count} auto-accepted Delta Learning members are "
                    f"woven into one {proposed_pv} Learning HIL candidate."
                ),
            },
            "project_choices": list(HIL_CHOICES),
            "learning_choices": list(HIL_CHOICES),
            "required_exact_approval_tokens": {
                "project": f"PROJECT {proposed_pv}: APPROVE",
                "learning": f"AI LEARNING {proposed_pv}: APPROVE",
            },
            "plan_hil_stamp_required": True,
            "plan_hil_stamp_scope": "PROJECT_AND_LEARNING_DECISIONS_SAME_PV",
            "accepted_archive_role": "POST_APPROVAL_SNAPSHOT_ONLY",
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "accepted_archive_model_context_source": False,
            "candidate_directory_created": False,
            "approval_inferred": False,
        }

    def complete_task_and_refresh(
        self,
        project_id: str,
        session_id: str,
        *,
        confirmation: str,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        """Confirm the final host source and seal its exit candidate in one step."""
        if batch_task_evidence is not None or batch_completion_confirmation is not None:
            require(
                batch_task_evidence is not None
                and batch_completion_confirmation is not None,
                "BATCH_DELTA_REFRESH_ARGUMENTS_INVALID",
                "Batch completion requires both exact evidence and confirmation.",
                status="BLOCKED",
            )
            exact_batch_evidence = cast(list[dict[str, Any]], batch_task_evidence)
            exact_batch_confirmation = cast(str, batch_completion_confirmation)
            self.store.validate_backlog_batch_completion(
                project_id,
                task_evidence=exact_batch_evidence,
                confirmation=exact_batch_confirmation,
            )
        confirmed = self.sessions.confirm_source_update(
            project_id,
            session_id,
            confirmation=confirmation,
        )
        pending_session = self.sessions.load(project_id, session_id)
        if (
            pending_session.candidate_id is not None
            and pending_session.state.value.endswith("_CANDIDATE")
        ):
            reconciled = (
                self.sessions.reconcile_pending_hil_candidate_after_lifecycle_append(
                    project_id,
                    session_id,
                )
            )
            return {
                **reconciled,
                "automatic_refresh": False,
                "candidate_rebuilt": False,
                "candidate_id_preserved": True,
                "candidate_history_preserved": True,
                "user_refresh_command_required": False,
                "source_confirmation": confirmed,
                "next_action": "PRESENT_SIX_WAY_HIL",
                "suggested_next_prompt": HIL_SUGGESTED_PROMPT,
                "next_action_contract": reconciled["candidate"]["next_action"],
                "hil_choices": list(HIL_CHOICES),
                **(
                    {
                        "dual_hil_presentation": self._dual_hil_presentation(
                            project_id,
                            candidate=reconciled["candidate"],
                        )
                    }
                    if self.store.uses_external_project_authority(project_id)
                    else {}
                ),
            }
        refreshed = self._refresh_hil_candidate(
            project_id,
            session_id,
            batch_task_evidence=batch_task_evidence,
            batch_completion_confirmation=batch_completion_confirmation,
        )
        return {
            **refreshed,
            "automatic_refresh": True,
            "user_refresh_command_required": False,
            "source_confirmation": confirmed,
            "next_action": "PRESENT_SIX_WAY_HIL",
            "suggested_next_prompt": HIL_SUGGESTED_PROMPT,
            "next_action_contract": refreshed["candidate"]["next_action"],
            "hil_choices": list(HIL_CHOICES),
        }

    def decide(
        self,
        project_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self.sessions.decide(project_id, session_id, **kwargs)
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["decision_persistence"] = sync.sync_receipt(
                project_id, result["decision"]
            )
            if result["pointer_advanced"] or result["pointer_moved"]:
                result["accepted_persistence"] = sync.sync_pv(
                    project_id,
                    result["pointer"]["accepted_pv"],
                    category="accepted",
                )
                result["pointer_persistence"] = sync.sync_pointer(project_id)
        if result["candidate_promoted"]:
            result["automatic_state_travel"] = False
            result["session"] = self.sessions.load(project_id, session_id).as_dict()
        return result

    def record_hil_decision(
        self, project_id: str, session_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Record non-promotion HIL outcomes; APPROVE is exclusive to Fuse."""

        require(
            str(kwargs.get("decision") or "") != "APPROVE",
            "APPROVE_REQUIRES_PV_FUSE",
            "Exact APPROVE may promote only through pv_fuse; continuation, a tool "
            "default, or hil_decide can never imply approval.",
            status="BLOCKED",
        )
        return self.decide(project_id, session_id, **kwargs)

    def fuse(
        self,
        project_id: str,
        session_id: str,
        *,
        approval: str,
        decided_by: str,
        decision_id: str | None = None,
        require_dual_learning_hil: bool = False,
    ) -> dict[str, Any]:
        require(
            approval == "APPROVE",
            "PV_FUSE_EXACT_APPROVE_REQUIRED",
            "PV Fuse requires the exact case-sensitive token APPROVE.",
            status="BLOCKED",
            provided=approval,
        )
        dual_learning_hil: dict[str, Any] = {
            "status": "NOT_REQUIRED_LEGACY_INTERNAL_CALL",
            "required": False,
        }
        if require_dual_learning_hil:
            session = self.sessions.load(project_id, session_id)
            require(
                session.candidate_id is not None,
                "PV_FUSE_PROJECT_CANDIDATE_REQUIRED",
                "Dual HIL Fuse requires the exact pending Project proposal.",
                status="BLOCKED",
            )
            project_candidate = self.store.candidate_validation(
                project_id, str(session.candidate_id)
            )
            proposed_pv = str(project_candidate.get("proposed_pv") or "")
            learning = inspect_learning_authority(
                self.store.project_root(project_id),
                project_id=project_id,
            )
            matching_weaves = [
                dict(row)
                for row in list(learning.get("learning_weaves") or [])
                if row.get("target_project_pv") == proposed_pv
                and row.get("weave_candidate_state") == "ACCEPTED"
            ]
            learning_pointer = dict(learning.get("current_pointer") or {})
            require(
                len(matching_weaves) == 1
                and learning_pointer.get("accepted_candidate_id")
                == matching_weaves[0].get("weave_candidate_id")
                and bool(
                    matching_weaves[0].get(
                        "acceptance_decision_receipt_sha256"
                    )
                ),
                "PV_FUSE_DUAL_LEARNING_HIL_APPROVAL_REQUIRED",
                "Project Fuse requires the separately recorded exact APPROVE for "
                "the one consolidated Learning weave targeting the same PV.",
                status="BLOCKED",
                proposed_pv=proposed_pv,
                matching_accepted_weave_count=len(matching_weaves),
                learning_pointer_candidate_id=learning_pointer.get(
                    "accepted_candidate_id"
                ),
            )
            weave = matching_weaves[0]
            dual_learning_hil = {
                "status": "PASS",
                "required": True,
                "project_target_pv": proposed_pv,
                "learning_weave_candidate_id": weave["weave_candidate_id"],
                "learning_weave_candidate_sha256": weave[
                    "weave_candidate_sha256"
                ],
                "learning_weave_receipt_sha256": weave["receipt_sha256"],
                "learning_approval_receipt_sha256": weave[
                    "acceptance_decision_receipt_sha256"
                ],
                "learning_approved_at": weave["acceptance_decided_at"],
                "learning_member_count": weave["member_count"],
                "learning_pointer_generation": learning_pointer.get("generation"),
                "learning_pointer_moved_by_project_fuse": False,
                "individual_delta_learning_hil_invoked": False,
            }
        result = self.decide(
            project_id,
            session_id,
            decision="APPROVE",
            decided_by=decided_by,
            decision_id=decision_id,
            dual_learning_hil=(
                dual_learning_hil if require_dual_learning_hil else None
            ),
        )
        return {
            "status": "PASS",
            "fused": True,
            "exact_approval": approval,
            "decision": result["decision"],
            "pointer": result["pointer"],
            "automatic_state_travel": False,
            "candidate_promoted": True,
            "pointer_moved": result["pointer_moved"],
            "dual_learning_hil": dual_learning_hil,
            "dual_hil_plan_stamp": result["dual_hil_plan_stamp"],
        }

    def prompt_index_status(
        self,
        project_id: str,
        session_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        session = self.sessions.load(project_id, session_id)
        host_session_id = str(
            session.metadata.get("current_host_session_id") or ""
        ).strip()
        require(
            bool(host_session_id),
            "HOST_SESSION_ID_NOT_BOUND",
            "Run /evi in this host task to bind its SessionStart ID before reading "
            "the prompt index.",
            status="BLOCKED",
        )
        return PromptIndex(self.store.root).status(
            host_session_id=host_session_id,
            project_id=project_id,
            evidence_session_id=session_id,
            limit=limit,
        )

    def rollback(
        self,
        project_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        rollback_mode = str(
            kwargs.get("rollback_mode") or "LOGICAL_LIVE_ROOT_STATE"
        ).upper()
        if self.store.uses_external_project_authority(project_id):
            result = self.sessions.rollback_live_root_state(
                project_id,
                session_id,
                **kwargs,
            )
        else:
            require(
                rollback_mode == "LOGICAL_LIVE_ROOT_STATE",
                "HARD_ROLLBACK_EXTERNAL_PROJECT_REQUIRED",
                "Hard ZIP and Git rollback modes require an external Project/PV root.",
                status="BLOCKED",
            )
            result = self.sessions.rollback_state(
                project_id,
                session_id,
                decided_by=str(kwargs["decided_by"]),
                rollback_to=kwargs.get("rollback_to"),
                decision_id=kwargs.get("decision_id"),
            )
        session = self.sessions.load(project_id, session_id)
        if session.metadata["persistence_mode"] in {
            "google_drive",
            "configured_durable_connector",
        }:
            sync = self._required_sync_service()
            result["decision_persistence"] = sync.sync_receipt(
                project_id, result["decision"]
            )
            if result["pointer_moved"]:
                result["accepted_persistence"] = sync.sync_pv(
                    project_id,
                    result["pointer"]["accepted_pv"],
                    category="accepted",
                )
                result["pointer_persistence"] = sync.sync_pointer(project_id)
        return result

    def _required_sync_service(self) -> PVSyncService:
        if self.sync_service is None:
            raise EvidenceLaneError(
                "DURABLE_PERSISTENCE_NOT_CONFIGURED",
                "The governed session requires user-owned Google Drive persistence.",
                status="BLOCKED",
            )
        return self.sync_service


SERVICE_ROUTE_REVIEW_SCHEMA = "evidence-lane.service-route-review.v1"

# These are service entry points reached by declared native MCP workflows.  The
# list is intentionally independent from Python visibility: adding a public
# method to EvidenceLaneService must not silently make it a product capability.
SERVICE_MCP_WORKFLOW_METHODS = frozenset(
    {
        "adaptive_delta_exit",
        "boot_session",
        "build_initial",
        "classify_and_enter_delta",
        "classify_hil_intent",
        "classify_mode",
        "complete_task_and_refresh",
        "configure_lane_routes",
        "connector_plugin_catalog",
        "connector_plugin_drop",
        "connector_plugin_register",
        "connector_plugin_route",
        "connector_plugin_settings",
        "doctor",
        "direct_force_same_worktree_state_travel",
        "enroll_project",
        "fuse",
        "lane_catalog",
        "lane_fetch",
        "lane_search",
        "lane_status",
        "live_authority_search",
        "plan_tasks",
        "prompt_index_status",
        "record_hil_decision",
        "record_steer_delta",
        "register_project",
        "resume_session",
        "rollback",
        "runtime_activation_status",
        "session_flash_status",
        "source_custom_schema_compile",
        "source_git_commit_impact",
        "source_git_history_build",
        "source_graph_build",
        "source_graph_diff",
        "source_graph_impact",
        "source_identity_register",
        "source_intake",
        "source_intake_schema_configure",
        "source_sqlite_inspect",
        "status",
        "status_window",
        "storage_connector_inspect",
        "storage_connector_select",
        "sync_git_source",
        "task_backlog",
        "task_backlog_window",
        "transition_law",
        "transition_task",
    }
)

# The local SDK reaches this exact subset of the same public workflows.  SDK
# handler ownership remains separately validated by inspect_sdk_handler_parity.
SERVICE_SDK_WORKFLOW_METHODS = frozenset(
    {
        "agent_configuration_authority",
        "conversation_memory_authority",
        "classify_mode",
        "connector_plugin_catalog",
        "connector_plugin_route",
        "doctor",
        "fuse",
        "live_authority_search",
        "prompt_index_status",
        "project_authority_status",
        "project_pv_storage_status",
        "migrate_project_authority",
        "record_hil_decision",
        "rollback",
        "runtime_activation_status",
        "session_flash_status",
        "source_intake",
        "status_window",
        "storage_connector_inspect",
        "storage_connector_select",
        "task_backlog_window",
        "transition_law",
        "transition_task",
    }
)

# invoke is the canonical MCP result/error envelope, not an independently
# invokable product operation.
SERVICE_DISPATCH_BOUNDARY_METHODS = frozenset({"invoke"})

# decide is the shared implementation below two deliberately separate public
# HIL workflows.  Routing it directly would bypass the exact-APPROVE Fuse gate.
SERVICE_INTERNAL_ORCHESTRATION_METHODS = {
    "adaptive_delta_entry": {
        "workflow": "FIRST_CLASS_DELTA_ENTRY_BEFORE_SOURCE_WORK",
        "public_entrypoints": ["classify_and_enter_delta"],
        "reason": (
            "The existing task_classify MCP action owns automatic Delta entry; "
            "the orchestration method is not an additional public tool."
        ),
    },
    "decide": {
        "workflow": "SPLIT_PROJECT_HIL_DECISION_CORE",
        "public_entrypoints": ["record_hil_decision", "fuse"],
        "reason": (
            "Non-promotion outcomes are owned by record_hil_decision; exact "
            "APPROVE promotion is owned exclusively by fuse."
        ),
    },
}

def inspect_service_route_parity(
    service_type: type[EvidenceLaneService] = EvidenceLaneService,
) -> dict[str, Any]:
    """Classify every public service method without inventing product routes."""

    inventory = {
        name
        for name in dir(service_type)
        if not name.startswith("_") and callable(getattr(service_type, name, None))
    }
    internal = set(SERVICE_INTERNAL_ORCHESTRATION_METHODS)
    dispatch = set(SERVICE_DISPATCH_BOUNDARY_METHODS)
    mcp = set(SERVICE_MCP_WORKFLOW_METHODS)
    sdk = set(SERVICE_SDK_WORKFLOW_METHODS)
    workflow = mcp | sdk
    classified = workflow | dispatch | internal
    overlaps = sorted(
        (workflow & dispatch)
        | (workflow & internal)
        | (dispatch & internal)
    )
    unclassified = sorted(inventory - classified)
    unknown = sorted(classified - inventory)
    require(
        not overlaps and not unclassified and not unknown,
        "SERVICE_ROUTE_CLASSIFICATION_MISMATCH",
        "Every public service method must have one exact workflow classification.",
        status="MISMATCH",
        overlapping_methods=overlaps,
        unclassified_methods=unclassified,
        classified_methods_missing_from_service=unknown,
    )

    methods: list[dict[str, Any]] = []
    for name in sorted(inventory):
        if name in internal:
            contract = SERVICE_INTERNAL_ORCHESTRATION_METHODS[name]
            methods.append(
                {
                    "method": name,
                    "classification": "INTERNAL_ORCHESTRATION_ONLY",
                    "workflow": contract["workflow"],
                    "route_owners": list(contract["public_entrypoints"]),
                    "public_route_eligible": False,
                    "implementation_delta_eligible": False,
                    "reason": contract["reason"],
                }
            )
            continue
        if name in dispatch:
            methods.append(
                {
                    "method": name,
                    "classification": "MCP_RESULT_BOUNDARY",
                    "workflow": "CANONICAL_TOOL_AND_LIFECYCLE_ENVELOPE",
                    "route_owners": ["MCP_DISPATCH"],
                    "public_route_eligible": False,
                    "implementation_delta_eligible": False,
                    "reason": "Shared dispatch/error boundary, not a product operation.",
                }
            )
            continue
        owners: list[str] = []
        if name in mcp:
            owners.append("NATIVE_MCP")
        if name in sdk:
            owners.append("INTERNAL_SDK")
        if name in mcp and name in sdk:
            classification = "MCP_AND_INTERNAL_SDK_WORKFLOW"
        elif name in mcp:
            classification = "MCP_WORKFLOW"
        else:
            classification = "INTERNAL_SDK_WORKFLOW"
        methods.append(
            {
                "method": name,
                "classification": classification,
                "workflow": "DECLARED_PUBLIC_WORKFLOW",
                "route_owners": owners,
                "public_route_eligible": name in mcp,
                "implementation_delta_eligible": False,
                "reason": "The method is already reached by its declared workflow.",
            }
        )

    body = {
        "schema": SERVICE_ROUTE_REVIEW_SCHEMA,
        "status": "PASS",
        "service_public_method_count": len(inventory),
        "mcp_workflow_method_count": len(mcp),
        "sdk_workflow_method_count": len(sdk),
        "dispatch_boundary_method_count": len(dispatch),
        "internal_orchestration_method_count": len(internal),
        "compatibility_route_method_count": 0,
        "eligible_unrouted_method_count": 0,
        "eligible_unrouted_methods": [],
        "implementation_delta_candidates": [],
        "methods": methods,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}
