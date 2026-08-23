"""Strict validation for one adaptive grouped local-install deferral."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .hook_contract import HOOK_EVENT_NAMES

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")


def adaptive_install_deferral_facts(
    receipt: Mapping[str, Any] | None,
    *,
    project_id: str,
    session_id: str,
    active_task_id: str,
    plan_tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return bounded facts; ``valid`` is false on every mismatch.

    This is deliberately narrower than a generic installed-surface exception.
    It accepts only a self-hashed adaptive-exit receipt that defers the exact
    active row to one later queued Plan row while preserving every protected
    authority.
    """

    supplied = dict(receipt or {})
    body = {key: value for key, value in supplied.items() if key != "receipt_sha256"}
    disposition = dict(supplied.get("install_disposition") or {})
    covered_task_ids = [
        str(value).strip()
        for value in disposition.get("covered_task_ids") or []
        if str(value).strip()
    ]
    deferred_to_task_id = str(
        disposition.get("deferred_to_task_id") or ""
    ).strip()
    by_id = {
        str(row.get("task_id") or ""): dict(row)
        for row in plan_tasks
        if str(row.get("task_id") or "").strip()
    }
    active = by_id.get(active_task_id) or {}
    deferred = by_id.get(deferred_to_task_id) or {}
    active_sequence = int(active.get("sequence") or -1)
    deferred_sequence = int(deferred.get("sequence") or -1)
    progression = [
        dict(row)
        for row in supplied.get("hook_progression") or []
        if isinstance(row, Mapping)
    ]
    hook_names = [str(row.get("hook_name") or "").strip() for row in progression]
    pointer_before = dict(supplied.get("pointer_before") or {})
    pointer_after = dict(supplied.get("pointer_after") or {})
    source_scope_sha256 = str(
        disposition.get("source_scope_sha256") or ""
    ).upper()
    receipt_sha256 = str(supplied.get("receipt_sha256") or "").upper()
    valid = (
        supplied.get("schema") == "evidence-lane.adaptive-delta-exit-receipt.v1"
        and supplied.get("status") == "PASS"
        and supplied.get("project_id") == project_id
        and supplied.get("session_id") == session_id
        and supplied.get("task_id") == active_task_id
        and _SHA256_RE.fullmatch(receipt_sha256) is not None
        and receipt_sha256 == sha256_bytes(canonical_json_bytes(body))
        and disposition.get("status") == "DEFERRED_TO_VERIFIED_BATCH"
        and disposition.get("install_performed") is False
        and active_task_id in covered_task_ids
        and len(covered_task_ids) == len(set(covered_task_ids))
        and all(task_id in by_id for task_id in covered_task_ids)
        and deferred.get("status") == "QUEUED"
        and active_sequence >= 0
        and deferred_sequence > active_sequence
        and _SHA256_RE.fullmatch(source_scope_sha256) is not None
        and pointer_before == pointer_after
        and supplied.get("repository_identity_unchanged") is True
        and supplied.get("candidate_created") is False
        and supplied.get("pending_hil_mutated") is False
        and supplied.get("pointer_moved") is False
        and supplied.get("hil_inferred") is False
        and supplied.get("git_mutated") is False
        and supplied.get("plan_task_advanced") is False
        and supplied.get("hook_registry_count") == len(progression)
        and len(progression) == len(HOOK_EVENT_NAMES)
        and all(hook_names)
        and len(hook_names) == len(set(hook_names))
        and set(hook_names) == set(HOOK_EVENT_NAMES)
        and all(
            row.get("state") == "UNCHANGED_INACTIVE"
            and row.get("verification_status") == "UNVERIFIED"
            for row in progression
        )
    )
    return {
        "valid": valid,
        "receipt_sha256": receipt_sha256,
        "deferred_to_task_id": deferred_to_task_id,
        "covered_task_ids": covered_task_ids,
        "source_scope_sha256": source_scope_sha256,
        "installed_surface_may_lag_source": valid,
    }
