"""Single live-root, current-authority query route for prompts, SDK, MCP, and exit."""

from __future__ import annotations

import json
from typing import Any

from .authority_support import validate_delta_exit_authority_supports
from .errors import require
from .git_adapter import inspect_repository
from .hashing import canonical_json_bytes, sha256_bytes
from .internal_sdk import build_live_local_sdk_context
from .lanes import CANONICAL_LANE_IDS
from .project_authority import query_working_project_sectors
from .timeutil import utc_now

LIVE_AUTHORITY_QUERY_SCHEMA = "evidence-lane.live-root-current-authority-query.v2"
_READ_OPERATIONS = (
    ("agent_learning", "retrieve"),
    ("project_memory", "query"),
    ("canon_input", "graph"),
    ("project_universe", "query"),
)
_REFRESH_OPERATIONS = (
    ("agent_learning", "bootstrap_verified_history"),
    ("canon_input", "bootstrap_consequence_graph"),
    ("project_memory", "bootstrap"),
    ("project_universe", "refresh"),
)


def _active_session_id(service: Any, project_id: str, session_id: str | None) -> str:
    exact = str(session_id or "").strip()
    if exact:
        return exact
    path = service.store.project_root(project_id) / "active_session.json"
    require(
        path.is_file(),
        "LIVE_AUTHORITY_ACTIVE_SESSION_REQUIRED",
        "Live-root authority query requires the exact active governed session.",
        status="MISMATCH",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    exact = str(payload.get("session_id") or "").strip()
    require(
        bool(exact),
        "LIVE_AUTHORITY_ACTIVE_SESSION_REQUIRED",
        "The active-session authority does not name a governed session.",
        status="MISMATCH",
    )
    return exact


def _bounded_public_data(module_id: str, data: dict[str, Any], limit: int) -> dict[str, Any]:
    if module_id in {"agent_learning", "project_memory"}:
        return {
            "status": data.get("status"),
            "result": data.get("result"),
            "hits": list(data.get("hits") or [])[:limit],
            "suppressed": list(data.get("suppressed") or [])[:limit],
            "search_engine": data.get("search_engine"),
            "full_ledger_loaded_into_model_context": data.get(
                "full_ledger_loaded_into_model_context", False
            ),
            "full_memory_loaded_into_model_context": data.get(
                "full_memory_loaded_into_model_context", False
            ),
        }
    if module_id == "project_universe":
        hits = list(data.get("hits") or [])[:limit]
        return {
            "status": data.get("status"),
            "result": "HIT" if hits else "NO_HIT",
            "hits": hits,
            "graph_sha256": data.get("graph_sha256"),
            "full_graph_returned": data.get("full_graph_returned", False),
        }
    consequence = dict(data.get("consequence_graph") or {})
    return {
        "status": consequence.get("status") or data.get("status"),
        "result": consequence.get("result"),
        "state": consequence.get("state"),
        "hits": list(consequence.get("hits") or [])[:limit],
        "counts": consequence.get("counts"),
        "graph_fingerprint_sha256": consequence.get("graph_fingerprint_sha256"),
        "graph_sha256": consequence.get("graph_sha256"),
        "search_engine": consequence.get("search_engine"),
        "full_graph_loaded_into_model_context": consequence.get(
            "full_graph_loaded_into_model_context", False
        ),
    }


def _apply_active_task_freshness(
    module_id: str,
    data: dict[str, Any],
    *,
    active_task_id: str,
) -> tuple[dict[str, Any], bool]:
    """Suppress a Project Memory hit that claims the wrong active Plan task."""

    if module_id != "project_memory":
        return data, False
    expected = f"plan://task/{active_task_id}"
    hits = list(data.get("hits") or [])
    active_hits = [
        row
        for row in hits
        if str(row.get("locator_kind") or "").strip().upper() == "ACTIVE_TASK"
    ]
    stale = [
        row
        for row in active_hits
        if str(row.get("locator_value") or "").strip() != expected
    ]
    if not stale:
        return {**data, "active_task_freshness": "PASS"}, False
    stale_ids = {str(row.get("locator_id") or "") for row in stale}
    fresh_hits = [
        row for row in hits if str(row.get("locator_id") or "") not in stale_ids
    ]
    suppressed = list(data.get("suppressed") or [])
    suppressed.extend(
        {
            "reason": "PROJECT_MEMORY_ACTIVE_TASK_MISMATCH",
            "locator_id": row.get("locator_id"),
            "revision_sha256": row.get("revision_sha256"),
            "expected_active_task_locator_sha256": sha256_bytes(
                expected.encode("utf-8")
            ),
        }
        for row in stale
    )
    return (
        {
            **data,
            "result": "HIT" if fresh_hits else "NO_HIT",
            "hits": fresh_hits,
            "suppressed": suppressed,
            "active_task_freshness": "STALE_HITS_SUPPRESSED",
            "stale_active_task_hit_count": len(stale),
        },
        True,
    )


def _read_arms(
    service: Any,
    *,
    project_id: str,
    session_id: str,
    query: str,
    limit: int,
    request_seed: str,
    pass_number: int,
) -> tuple[list[dict[str, Any]], Any]:
    sdk, binding = build_live_local_sdk_context(
        service,
        project_id=project_id,
        session_id=session_id,
    )
    as_of = utc_now()
    route_fingerprint = str(binding.public_surface_registry_sha256 or "")[:16].lower()
    payloads = {
        ("agent_learning", "retrieve"): {
            "query": query,
            "scope_selectors": [binding.task_id],
            "as_of": as_of,
            "limit": limit,
        },
        ("project_memory", "query"): {
            "query": query,
            "as_of": as_of,
            "limit": limit,
        },
        ("canon_input", "graph"): {"query": query, "limit": limit},
        ("project_universe", "query"): {"query": query, "limit": limit},
    }
    rows: list[dict[str, Any]] = []
    for ordinal, (module_id, operation) in enumerate(_READ_OPERATIONS, start=1):
        payload = payloads[(module_id, operation)]
        payload_fingerprint = sha256_bytes(canonical_json_bytes(payload))[
            :16
        ].lower()
        response = sdk.invoke(
            module_id=module_id,
            operation=operation,
            binding=binding,
            payload=payload,
            request_id=(
                f"live-query:{request_seed}:{route_fingerprint}:read:"
                f"{pass_number}:{ordinal}:{payload_fingerprint}"
            ),
            timeout_ms=30_000,
        )
        status = str(response.get("status") or "").strip().upper()
        require(
            status in {"PASS", "STALE"}
            and response.get("module_id") == module_id
            and response.get("operation") == operation,
            "LIVE_AUTHORITY_ARM_QUERY_FAILED",
            "A current-authority query arm failed its exact SDK route.",
            status="FAIL",
            module_id=module_id,
            operation=operation,
        )
        data = _bounded_public_data(
            module_id, dict(response.get("data") or {}), limit
        )
        data, active_task_stale = _apply_active_task_freshness(
            module_id,
            data,
            active_task_id=str(binding.task_id),
        )
        result_state = str(
            data.get("result") or data.get("state") or data.get("status") or status
        ).strip().upper()
        requires_refresh = bool(
            status == "STALE"
            or active_task_stale
            or str(data.get("status") or "").strip().upper() == "STALE"
            or result_state in {
                "NO_HIT",
                "EMPTY",
                "STALE",
                "INCOMPLETE",
                "NO_CONSEQUENCE_GRAPH",
                "CONSEQUENCE_GRAPH_QUERY_INDEX_REFRESH_REQUIRED",
                "CONSEQUENCE_GRAPH_POINTER_REFRESH_REQUIRED",
            }
        )
        rows.append(
            {
                "ordinal": ordinal,
                "authority": module_id,
                "operation": operation,
                "status": status,
                "result_state": result_state,
                "refresh_required": requires_refresh,
                "receipt_sha256": response.get("receipt_sha256"),
                "data": data,
                "authority_merge_allowed": False,
            }
        )
    return rows, binding


def _refresh_arms(
    service: Any,
    *,
    project_id: str,
    session_id: str,
    request_seed: str,
) -> list[dict[str, Any]]:
    write_scope = tuple(
        f"{module_id}:{operation}" for module_id, operation in _REFRESH_OPERATIONS
    )
    sdk, binding = build_live_local_sdk_context(
        service,
        project_id=project_id,
        session_id=session_id,
        write_scope=write_scope,
    )
    rows: list[dict[str, Any]] = []
    route_fingerprint = str(binding.public_surface_registry_sha256 or "")[:16].lower()
    for ordinal, (module_id, operation) in enumerate(_REFRESH_OPERATIONS, start=1):
        response = sdk.invoke(
            module_id=module_id,
            operation=operation,
            binding=binding,
            payload={},
            request_id=(
                f"live-query:{request_seed}:{route_fingerprint}:refresh:{ordinal}"
            ),
            timeout_ms=60_000,
        )
        require(
            response.get("status") == "PASS"
            and response.get("module_id") == module_id
            and response.get("operation") == operation,
            "LIVE_AUTHORITY_REFRESH_FAILED",
            "Learning, Canon, Memory, and Universe must refresh once in governed order.",
            status="FAIL",
            module_id=module_id,
            operation=operation,
        )
        rows.append(
            {
                "ordinal": ordinal,
                "authority": module_id,
                "operation": operation,
                "status": "PASS",
                "receipt_sha256": response.get("receipt_sha256"),
                "authority_effects": response.get("authority_effects"),
            }
        )
    return rows


def query_live_authorities(
    service: Any,
    *,
    project_id: str,
    query: str,
    limit: int = 8,
    session_id: str | None = None,
    refresh_on_miss: bool = True,
) -> dict[str, Any]:
    """Query current governed authorities without opening accepted archives."""

    exact_query = str(query or "").strip()
    require(
        bool(exact_query) and 1 <= int(limit) <= 20,
        "LIVE_AUTHORITY_QUERY_BOUNDS_INVALID",
        "Live-root query requires lexical text and a limit from one to twenty.",
        status="BLOCKED",
    )
    exact_session = _active_session_id(service, project_id, session_id)
    authority_support = validate_delta_exit_authority_supports(
        service.store.project_root(project_id)
    )
    require(
        authority_support.get("status")
        in {"PASS", "PENDING_FIRST_DELTA_EXIT_ACTIVATION"},
        "LIVE_AUTHORITY_SUPPORT_SYSTEM_INVALID",
        "The live query requires intact auxiliary MMD/DOT/SQLite support systems.",
        status="MISMATCH",
    )
    seed = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "session_id": exact_session,
                "query": exact_query,
                "limit": int(limit),
            }
        )
    )[:32].lower()
    reads, binding = _read_arms(
        service,
        project_id=project_id,
        session_id=exact_session,
        query=exact_query,
        limit=int(limit),
        request_seed=seed,
        pass_number=1,
    )
    repository_path = service.store.config(project_id).repository_path
    repository = inspect_repository(repository_path)
    pointer = service.store.pointer(project_id)
    sectors = query_working_project_sectors(
        service.store.project_root(project_id),
        repository_root=repository_path,
        project_id=project_id,
        accepted_pv=str(pointer.accepted_pv),
        pointer_generation=int(pointer.generation),
        query=exact_query,
        lane_ids=list(CANONICAL_LANE_IDS),
        limit=int(limit),
        expected_branch=repository.branch,
        expected_head=repository.commit_sha,
    )
    require(
        sectors.get("status") in {"PASS", "EMPTY"}
        and sectors.get("query_mutated_project_authority") is False
        and sectors.get("query_rehashed_dirty_content") is False,
        "LIVE_AUTHORITY_SECTOR_QUERY_FAILED",
        "The root query requires one read-only all-eighteen-sector slice.",
        status="FAIL",
    )
    connector_brain = service.connector_plugin_catalog(project_id)
    require(
        connector_brain.get("status") == "PASS"
        and connector_brain.get("integrity") == ["ok"]
        and not connector_brain.get("foreign_key_errors"),
        "LIVE_AUTHORITY_CONNECTOR_BRAIN_QUERY_FAILED",
        "The live-root query requires an intact connector-brain authority.",
        status="FAIL",
    )
    refresh_required = any(row["refresh_required"] for row in reads)
    refresh_receipts: list[dict[str, Any]] = []
    retry_reads: list[dict[str, Any]] | None = None
    if refresh_on_miss and refresh_required:
        refresh_seed = sha256_bytes(
            canonical_json_bytes(
                {
                    "query_seed": seed,
                    "initial_read_receipts": [
                        row.get("receipt_sha256") for row in reads
                    ],
                }
            )
        )[:32].lower()
        refresh_receipts = _refresh_arms(
            service,
            project_id=project_id,
            session_id=exact_session,
            request_seed=refresh_seed,
        )
        retry_reads, binding = _read_arms(
            service,
            project_id=project_id,
            session_id=exact_session,
            query=exact_query,
            limit=int(limit),
            request_seed=seed,
            pass_number=2,
        )
    agent_configuration = service.agent_configuration_authority(
        project_id, session_id=exact_session
    )
    conversation_memory = service.conversation_memory_authority(
        project_id, session_id=exact_session
    )
    effective_reads = retry_reads or reads
    result_state = (
        "HITS"
        if sectors.get("hits")
        or any(row.get("data", {}).get("hits") for row in effective_reads)
        else "EMPTY"
    )
    return {
        "schema": LIVE_AUTHORITY_QUERY_SCHEMA,
        "status": "PASS" if result_state == "HITS" else "EMPTY",
        "result_state": result_state,
        "project_id": project_id,
        "session_id": exact_session,
        "active_task_id": binding.task_id,
        "query": exact_query,
        "bounded_result_limit_per_authority": int(limit),
        "env_uop": {
            "env_authority_sha256": binding.env_authority_sha256,
            "uop_authority_sha256": binding.uop_authority_sha256,
            "derived_projection_sha256": binding.derived_projection_sha256,
            "flash_receipt_sha256": binding.flash_receipt_sha256,
        },
        "env_uop_governance": {
            "role": "GOVERNING_CONTROL_PLANE_NOT_AUTHORITY_ARMS",
            "current_authority_classes": [
                "PROJECT_SECTORS_AND_ROOT_FILES",
                "AI_LEARNING",
                "CANON_GRAPH",
                "PROJECT_MEMORY_DB",
                "HOST_CONVERSATION_MEMORY_MD",
                "AGENTS_MD",
                "PROJECT_UNIVERSE",
                "CONNECTOR_BRAIN",
            ],
            "authority_class_set_derived_from_current_runtime": True,
            "hil_only_layers": ["PROJECT_OVERLAY"],
            "authority_merge_allowed": False,
        },
        "authority_support_systems": authority_support,
        "sector_lane_traversal": {
            "receipt_count": sectors.get("lane_traversal_receipt_count"),
            "all_queried_lanes_used_mmd_dot": sectors.get(
                "mmd_dot_traversal_used_for_every_queried_lane"
            ),
            "tools_role": sectors.get("tools_json_query_role"),
        },
        "authorities": {
            "sector_lanes": {
                "authority": "ALL_CURRENT_LIVE_ROOT_SECTORS",
                "result": sectors,
                "authority_merge_allowed": False,
            },
            "agent_learning": next(
                row for row in effective_reads if row["authority"] == "agent_learning"
            ),
            "canon_graph": next(
                row for row in effective_reads if row["authority"] == "canon_input"
            ),
            "project_memory": next(
                row for row in effective_reads if row["authority"] == "project_memory"
            ),
            "project_universe": next(
                row for row in effective_reads if row["authority"] == "project_universe"
            ),
            "connector_brain": {
                "authority": "CONNECTOR_BRAIN",
                "status": connector_brain.get("status"),
                "active_count": connector_brain.get("active_count"),
                "routable_count": connector_brain.get("routable_count"),
                "integrity": connector_brain.get("integrity"),
                "foreign_key_errors": connector_brain.get("foreign_key_errors"),
                "secret_values_persisted": connector_brain.get(
                    "secret_values_persisted"
                ),
                "authority_merge_allowed": False,
            },
            "agent_configuration": {
                "authority": "AGENTS_MD",
                "status": agent_configuration.get("status"),
                "authority_sha256": agent_configuration.get(
                    "agent_configuration_authority_sha256"
                ),
                "source_chain_sha256": agent_configuration.get(
                    "source_chain_sha256"
                ),
                "authority_merge_allowed": False,
            },
            "conversation_memory": {
                "authority": "HOST_CONVERSATION_MEMORY_MD",
                "status": conversation_memory.get("status"),
                "authority_sha256": conversation_memory.get(
                    "conversation_memory_authority_sha256"
                ),
                "source_chain_sha256": conversation_memory.get(
                    "source_chain_sha256"
                ),
                "authority_merge_allowed": False,
            },
        },
        "initial_reads": reads,
        "refresh_required": refresh_required,
        "refresh_performed": bool(refresh_receipts),
        "refresh_receipts": refresh_receipts,
        "bounded_retry_performed": retry_reads is not None,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
        "accepted_pointer_used_as_baseline_only": True,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "authority_merge_allowed": False,
    }
