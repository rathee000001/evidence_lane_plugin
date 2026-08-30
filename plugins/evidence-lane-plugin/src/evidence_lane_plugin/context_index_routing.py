"""Conditional context-index routing that never replaces SQLite authority."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

CONTEXT_INDEX_ROUTING_SCHEMA = "evidence-lane.context-index-routing.v1"

_SHA256 = re.compile(r"[A-F0-9]{64}")
_INDEXES: dict[str, dict[str, Any]] = {
    "SQLite_FTS5_BM25": {
        "kind": "local_authority_index",
        "credential_names": [],
        "transports": ["sqlite"],
    },
    "FAISS_CPU": {
        "kind": "local_rebuildable_vector_index",
        "credential_names": [],
        "transports": ["python"],
    },
    "Pinecone": {
        "kind": "remote_rebuildable_vector_index",
        "credential_names": ["PINECONE_API_KEY", "PINECONE_HOST"],
        "transports": ["https"],
    },
    "Weaviate": {
        "kind": "remote_rebuildable_hybrid_index",
        "credential_names": ["WEAVIATE_URL", "WEAVIATE_API_KEY"],
        "transports": ["https"],
    },
    "Milvus": {
        "kind": "remote_rebuildable_vector_index",
        "credential_names": ["MILVUS_URI", "MILVUS_TOKEN"],
        "transports": ["https", "grpc_adapter"],
    },
    "OpenSearch": {
        "kind": "remote_rebuildable_hybrid_index",
        "credential_names": [
            "OPENSEARCH_URL",
            "OPENSEARCH_USERNAME",
            "OPENSEARCH_PASSWORD",
        ],
        "transports": ["https"],
    },
}


def build_context_index_operation(
    *,
    tool_id: str,
    operation: str,
    project_id: str,
    authority_id: str,
    lane_id: str,
    sqlite_identity_sha256: str,
    source_sha256: str,
    granted_tools: Iterable[str],
    top_k: int = 20,
) -> dict[str, Any]:
    if tool_id not in _INDEXES:
        raise ValueError(f"Unknown context index: {tool_id}")
    exact_operation = operation.strip().lower()
    if exact_operation not in {"query", "upsert_changed", "delete_identity"}:
        raise ValueError("Context index operation is not supported.")
    for label, value in (
        ("sqlite_identity_sha256", sqlite_identity_sha256),
        ("source_sha256", source_sha256),
    ):
        if _SHA256.fullmatch(value.strip().upper()) is None:
            raise ValueError(f"{label} must be an exact SHA-256.")
    if not project_id.strip() or not authority_id.strip() or not lane_id.strip():
        raise ValueError("Context routing requires project, authority, and lane identity.")
    if not 1 <= int(top_k) <= 200:
        raise ValueError("Context index top_k must be between 1 and 200.")
    granted = set(granted_tools)
    remote = str(_INDEXES[tool_id]["kind"]).startswith("remote_")
    if remote and tool_id not in granted:
        status = "BLOCKED_PROJECT_GRANT_REQUIRED"
    else:
        status = "PASS"
    body = {
        "schema": CONTEXT_INDEX_ROUTING_SCHEMA,
        "status": status,
        "tool_id": tool_id,
        "index_kind": _INDEXES[tool_id]["kind"],
        "operation": exact_operation,
        "project_id": project_id.strip(),
        "authority_id": authority_id.strip(),
        "lane_id": lane_id.strip(),
        "sqlite_identity_sha256": sqlite_identity_sha256.strip().upper(),
        "source_sha256": source_sha256.strip().upper(),
        "top_k": int(top_k),
        "credential_names": list(_INDEXES[tool_id]["credential_names"]),
        "credential_values_read": False,
        "transports": list(_INDEXES[tool_id]["transports"]),
        "sqlite_remains_durable_authority": True,
        "index_is_rebuildable_from_sqlite_and_source_hashes": True,
        "project_truth_promotion_allowed": False,
        "cross_project_namespace_allowed": False,
        "changed_hash_only_write": exact_operation == "upsert_changed",
        "network_call_performed": False,
    }
    return {**body, "route_sha256": sha256_bytes(canonical_json_bytes(body))}


def bind_context_results_to_sqlite_identity(
    *,
    sqlite_identity_sha256: str,
    tool_id: str,
    result_ids: Iterable[str],
) -> dict[str, Any]:
    exact_hash = sqlite_identity_sha256.strip().upper()
    if _SHA256.fullmatch(exact_hash) is None:
        raise ValueError("Result binding requires an exact SQLite identity SHA-256.")
    if tool_id not in _INDEXES:
        raise ValueError(f"Unknown context index: {tool_id}")
    ids = list(dict.fromkeys(str(value).strip() for value in result_ids if str(value).strip()))
    body = {
        "schema": "evidence-lane.context-index-result-binding.v1",
        "status": "PASS",
        "tool_id": tool_id,
        "sqlite_identity_sha256": exact_hash,
        "result_ids": ids,
        "result_count": len(ids),
        "result_order_preserved": True,
        "results_are_evidence_locators_not_authority": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def context_index_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.context-index-catalog.v1",
        "status": "PASS",
        "index_count": len(_INDEXES),
        "indexes": [
            {"tool_id": tool_id, **contract} for tool_id, contract in _INDEXES.items()
        ],
        "sqlite_remains_authority": True,
        "all_non_sqlite_indexes_are_rebuildable": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "CONTEXT_INDEX_ROUTING_SCHEMA",
    "bind_context_results_to_sqlite_identity",
    "build_context_index_operation",
    "context_index_catalog",
]
