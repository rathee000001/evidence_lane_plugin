"""Replay-safe universal per-Delta adaptive exit orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .errors import require
from .git_adapter import inspect_repository
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .hook_contract import HOOK_EVENT_NAMES
from .host_plan_rehydration import prepare_host_plan_rehydration
from .internal_sdk import build_live_local_sdk_context

ADAPTIVE_DELTA_EXIT_SCHEMA = "evidence-lane.adaptive-delta-exit-receipt.v1"
_SHA256_HEX = frozenset("0123456789ABCDEF")
_INSTALL_STATES = frozenset(
    {
        "VERIFIED_LOCAL_TESTING_INSTALL",
        "DEFERRED_TO_VERIFIED_BATCH",
        "NOT_CODE_BEARING",
    }
)
_HOOK_STATES = frozenset(
    {
        "UNCHANGED_INACTIVE",
        "VERIFIED_ENABLED",
        "FAILED_DISABLED_NATIVE_FALLBACK",
    }
)
_SDK_OPERATIONS = (
    ("agent_learning", "bootstrap_verified_history"),
    ("canon_input", "bootstrap_consequence_graph"),
    ("project_memory", "bootstrap"),
    ("project_universe", "refresh"),
)


def _required_sha256(value: object, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        len(exact) == 64 and set(exact) <= _SHA256_HEX,
        "ADAPTIVE_DELTA_EXIT_HASH_INVALID",
        "Adaptive Delta exit evidence requires exact SHA-256 identities.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _active_row(backlog: Mapping[str, Any], *, task_id: str) -> dict[str, Any]:
    rows = cast(list[dict[str, Any]], backlog["goal_projection"]["rows"])
    active = [row for row in rows if row.get("status") == "in_progress"]
    require(
        len(active) == 1 and active[0].get("task_id") == task_id,
        "ADAPTIVE_DELTA_EXIT_ACTIVE_BINDING_MISMATCH",
        "Adaptive Delta exit requires the exact sole active Plan task.",
        status="MISMATCH",
        active_task_ids=[row.get("task_id") for row in active],
        requested_task_id=task_id,
    )
    return active[0]


def _validators(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    require(
        0 < len(rows) <= 32,
        "ADAPTIVE_DELTA_EXIT_VALIDATORS_REQUIRED",
        "Adaptive Delta exit requires a bounded non-empty validator receipt set.",
        status="BLOCKED",
    )
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        require(
            isinstance(row, dict)
            and str(row.get("status") or "").strip().upper() == "PASS"
            and bool(str(row.get("name") or "").strip())
            and bool(str(row.get("evidence_locator") or "").strip()),
            "ADAPTIVE_DELTA_EXIT_VALIDATOR_FAILED",
            "Every targeted validator must carry a PASS receipt and evidence locator.",
            status="BLOCKED",
            validator_index=index,
        )
        normalized.append(
            {
                **row,
                "status": "PASS",
                "receipt_sha256": _required_sha256(
                    row.get("receipt_sha256"),
                    field=f"validator_results[{index}].receipt_sha256",
                ),
            }
        )
    return normalized


def _install_disposition(
    value: dict[str, Any],
    *,
    active: dict[str, Any],
    goal_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    exact = dict(value or {})
    state = str(exact.get("status") or "").strip().upper()
    require(
        state in _INSTALL_STATES,
        "ADAPTIVE_DELTA_EXIT_INSTALL_DISPOSITION_INVALID",
        "Adaptive Delta exit requires a verified install, governed batch deferral, or exact non-code classification.",
        status="BLOCKED",
    )
    exact["status"] = state
    exact["source_scope_sha256"] = _required_sha256(
        exact.get("source_scope_sha256"),
        field="install_disposition.source_scope_sha256",
    )
    if state == "DEFERRED_TO_VERIFIED_BATCH":
        deferred_to = str(exact.get("deferred_to_task_id") or "").strip()
        covered = list(
            dict.fromkeys(
                str(item).strip()
                for item in exact.get("covered_task_ids") or []
                if str(item).strip()
            )
        )
        target = next(
            (row for row in goal_rows if row.get("task_id") == deferred_to),
            None,
        )
        require(
            target is not None
            and target.get("status") == "pending"
            and int(target["number"]) > int(active["number"])
            and str(active["task_id"]) in covered
            and bool(str(exact.get("reason") or "").strip()),
            "ADAPTIVE_DELTA_EXIT_INSTALL_DEFERRAL_INVALID",
            "A grouped install deferral must bind a later queued task and cover the active Delta.",
            status="MISMATCH",
            deferred_to_task_id=deferred_to or None,
        )
        exact["deferred_to_task_id"] = deferred_to
        exact["covered_task_ids"] = covered
        exact["install_performed"] = False
    elif state == "VERIFIED_LOCAL_TESTING_INSTALL":
        for field in (
            "package_sha256",
            "catalog_sha256",
            "runtime_sha256",
            "task_binding_sha256",
        ):
            exact[field] = _required_sha256(
                exact.get(field),
                field=f"install_disposition.{field}",
            )
        exact["install_performed"] = True
    else:
        require(
            bool(str(exact.get("reason") or "").strip()),
            "ADAPTIVE_DELTA_EXIT_NON_CODE_REASON_REQUIRED",
            "A non-code Delta classification requires its exact reason.",
            status="BLOCKED",
        )
        exact["install_performed"] = False
    return exact


def _hook_progression(
    rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    exact_rows = (
        list(rows)
        if rows is not None
        else [
            {
                "hook_name": name,
                "state": "UNCHANGED_INACTIVE",
                "verification_status": "UNVERIFIED",
                "receipt_sha256": None,
            }
            for name in HOOK_EVENT_NAMES
        ]
    )
    names = [str(row.get("hook_name") or "") for row in exact_rows]
    require(
        len(names) == len(HOOK_EVENT_NAMES)
        and len(set(names)) == len(names)
        and set(names) == set(HOOK_EVENT_NAMES),
        "ADAPTIVE_DELTA_EXIT_HOOK_REGISTRY_MISMATCH",
        "Hook progression must report every current registry event exactly once.",
        status="MISMATCH",
        expected_hook_names=list(HOOK_EVENT_NAMES),
    )
    normalized: list[dict[str, Any]] = []
    for row in exact_rows:
        hook_name = str(row.get("hook_name") or "")
        state = str(row.get("state") or "").strip().upper()
        verification = str(row.get("verification_status") or "").strip().upper()
        receipt_hash = row.get("receipt_sha256")
        require(
            state in _HOOK_STATES
            and verification in {"UNVERIFIED", "PASS", "FAIL"}
            and (state != "VERIFIED_ENABLED" or verification == "PASS")
            and (verification == "UNVERIFIED" or bool(receipt_hash)),
            "ADAPTIVE_DELTA_EXIT_HOOK_STATE_INVALID",
            "Each hook must remain independently named and explicitly verified or inactive.",
            status="MISMATCH",
            hook_name=hook_name,
        )
        normalized.append(
            {
                "hook_name": hook_name,
                "state": state,
                "verification_status": verification,
                "receipt_sha256": (
                    _required_sha256(
                        receipt_hash,
                        field=f"hook_progression.{hook_name}.receipt_sha256",
                    )
                    if receipt_hash
                    else None
                ),
            }
        )
    return normalized


def _sdk_refreshes(
    service: Any,
    *,
    project_id: str,
    session_id: str,
    request_seed: str,
) -> list[dict[str, Any]]:
    write_scope = tuple(
        f"{module}:{operation}" for module, operation in _SDK_OPERATIONS
    )
    sdk, binding = build_live_local_sdk_context(
        service,
        project_id=project_id,
        session_id=session_id,
        write_scope=write_scope,
    )
    receipts: list[dict[str, Any]] = []
    for ordinal, (module_id, operation) in enumerate(_SDK_OPERATIONS, start=1):
        response = sdk.invoke(
            module_id=module_id,
            operation=operation,
            binding=binding,
            payload={},
            request_id=f"delta-exit:{request_seed}:{ordinal}",
            timeout_ms=60_000,
        )
        require(
            response.get("status") == "PASS"
            and response.get("module_id") == module_id
            and response.get("operation") == operation,
            "ADAPTIVE_DELTA_EXIT_INTELLIGENCE_REFRESH_FAILED",
            "Learning, Canon, Memory, and Universe must refresh in governed order.",
            status="FAIL",
            module_id=module_id,
            operation=operation,
        )
        receipts.append(
            {
                "ordinal": ordinal,
                "module_id": module_id,
                "operation": operation,
                "status": "PASS",
                "receipt_sha256": _required_sha256(
                    response.get("receipt_sha256"),
                    field=f"intelligence_receipts[{ordinal}].receipt_sha256",
                ),
                "authority_effects": response.get("authority_effects"),
            }
        )
    return receipts


def _write_immutable_receipt(path: Path, receipt: dict[str, Any]) -> None:
    if path.is_file():
        require(
            json.loads(path.read_text(encoding="utf-8")) == receipt,
            "ADAPTIVE_DELTA_EXIT_RECEIPT_CONFLICT",
            "The immutable adaptive-exit receipt path contains other bytes.",
            status="MISMATCH",
        )
        return
    atomic_write_json(path, receipt)


def run_adaptive_delta_exit(
    service: Any,
    project_id: str,
    session_id: str,
    *,
    task_id: str,
    source_event_id: str,
    prior_formula_sha256: str,
    formula: dict[str, Any],
    validator_results: list[dict[str, Any]],
    install_disposition: dict[str, Any],
    actor: str = "ADAPTIVE_DELTA_EXIT",
    hook_progression: list[dict[str, Any]] | None = None,
    fixed_window_task_ids: list[str] | None = None,
    observed_host_plan: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Refresh isolated intelligence arms and close one exact Delta formula."""

    exact_task_id = str(task_id or "").strip()
    exact_source_event_id = str(source_event_id or "").strip()
    exact_actor = str(actor or "").strip()
    exact_prior_formula = _required_sha256(
        prior_formula_sha256,
        field="prior_formula_sha256",
    )
    require(
        bool(exact_task_id)
        and bool(exact_source_event_id)
        and bool(exact_actor)
        and isinstance(formula, dict),
        "ADAPTIVE_DELTA_EXIT_BINDING_INVALID",
        "Adaptive Delta exit requires exact task, event, actor, and formula bindings.",
        status="BLOCKED",
    )
    session_before = service.sessions.load(project_id, session_id)
    pointer_before = service.store.pointer(project_id).as_dict()
    backlog_before = service.store.backlog_status(project_id)
    active_before = _active_row(backlog_before, task_id=exact_task_id)
    require(
        session_before.candidate_id is None
        and not session_before.state.value.endswith("_CANDIDATE"),
        "ADAPTIVE_DELTA_EXIT_CANDIDATE_PRESENT",
        "Adaptive Delta exit cannot run while a candidate or HIL is pending.",
        status="MISMATCH",
        candidate_id=session_before.candidate_id,
    )
    repository_path = service.store.config(project_id).repository_path
    repository_before = inspect_repository(repository_path).as_dict()
    exact_validators = _validators(validator_results)
    goal_rows = cast(list[dict[str, Any]], backlog_before["goal_projection"]["rows"])
    exact_install = _install_disposition(
        install_disposition,
        active=active_before,
        goal_rows=goal_rows,
    )
    exact_hooks = _hook_progression(hook_progression)
    request_seed = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "session_id": session_id,
                "task_id": exact_task_id,
                "source_event_id": exact_source_event_id,
                "prior_formula_sha256": exact_prior_formula,
            }
        )
    )[:32].lower()
    intelligence = _sdk_refreshes(
        service,
        project_id=project_id,
        session_id=session_id,
        request_seed=request_seed,
    )
    host_task_id = str(
        session_before.metadata.get("current_host_session_id") or ""
    ).strip()
    require(
        bool(host_task_id),
        "ADAPTIVE_DELTA_EXIT_HOST_TASK_REQUIRED",
        "Adaptive Delta exit requires the exact bound native host task.",
        status="MISMATCH",
    )
    host_result = prepare_host_plan_rehydration(
        service.store.root,
        project_id=project_id,
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="GOAL_ACTIVE_TURN",
        trigger_event_id=exact_source_event_id,
        observed_artifact=observed_host_plan,
        host_goal_active=True,
        fixed_window_task_ids=fixed_window_task_ids,
        reuse_previous_window=True,
    )
    host_receipt = cast(dict[str, Any], host_result["receipt"])
    projection = cast(dict[str, Any], host_receipt["projection"])
    require(
        host_receipt.get("status") == "PASS"
        and host_receipt.get("action")
        in {
            "NO_HOST_PLAN_ACTION_CURRENT_WINDOW_VISIBLE",
            "NO_HOST_PLAN_ACTION_REUSE_CURRENT_WINDOW",
            "ACTIVATE_HOST_PLAN_CURRENT_WINDOW",
            "REACTIVATE_EXISTING_HOST_PLAN_WINDOW",
            "SYNC_HOST_PLAN_WINDOW_AFTER_PLAN_STEER",
            "UPDATE_HOST_PLAN_CURRENT_WINDOW_STATUSES",
            "ADVANCE_HOST_PLAN_TO_NEXT_WINDOW",
        },
        "ADAPTIVE_DELTA_EXIT_HOST_PROJECTION_INVALID",
        "The host projection decision must be one exact PASS action or no-op.",
        status="MISMATCH",
    )
    formula_payload = {
        **formula,
        "validator_results": exact_validators,
        "code_test_install_receipts": {
            "validator_set_sha256": sha256_bytes(
                canonical_json_bytes(exact_validators)
            ),
            "install_disposition": exact_install,
        },
        "intelligence_refresh_receipts": intelligence,
        "host_window_ui_fingerprint_sha256": projection.get(
            "window_ui_fingerprint_sha256"
        ),
    }
    formula_receipt = service.store.record_task_formula(
        project_id,
        task_id=exact_task_id,
        event_kind="EXIT_FORMULA",
        source_event_id=exact_source_event_id,
        session_id=session_id,
        formula=formula_payload,
        actor=exact_actor,
        prior_formula_sha256=exact_prior_formula,
        changed_terms={},
        cause_evidence_locator=f"delta-exit://{request_seed}",
        event_id=event_id,
    )
    pointer_after = service.store.pointer(project_id).as_dict()
    session_after = service.sessions.load(project_id, session_id)
    backlog_after = service.store.backlog_status(project_id)
    _active_row(backlog_after, task_id=exact_task_id)
    repository_after = inspect_repository(repository_path).as_dict()
    identity_fields = ("branch", "commit_sha", "tree_sha", "worktree_sha256")
    repository_unchanged = all(
        repository_before.get(field) == repository_after.get(field)
        for field in identity_fields
    )
    require(
        pointer_after == pointer_before
        and session_after.candidate_id is None
        and not session_after.state.value.endswith("_CANDIDATE")
        and repository_unchanged,
        "ADAPTIVE_DELTA_EXIT_POSTCONDITION_MISMATCH",
        "Adaptive Delta exit changed a protected pointer, candidate, Plan row, or repository identity.",
        status="MISMATCH",
    )
    formula_event = cast(dict[str, Any], formula_receipt["event"])
    goal_projection_after = cast(
        dict[str, Any], backlog_after["goal_projection"]
    )
    receipt_body = {
        "schema": ADAPTIVE_DELTA_EXIT_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "session_id": session_id,
        "host_task_id": host_task_id,
        "task_id": exact_task_id,
        "source_event_id": exact_source_event_id,
        "formula_event_id": formula_event["event_id"],
        "formula_event_sha256": formula_event["event_sha256"],
        "formula_sha256": formula_event["formula_sha256"],
        "validator_set_sha256": sha256_bytes(canonical_json_bytes(exact_validators)),
        "validator_count": len(exact_validators),
        "install_disposition": exact_install,
        "intelligence_refreshes": intelligence,
        "hook_progression": exact_hooks,
        "hook_registry_count": len(exact_hooks),
        "host_plan": {
            "action": host_receipt.get("action"),
            "host_update_plan_required": host_receipt.get("host_update_plan_required"),
            "window_task_ids": projection.get("window_task_ids"),
            "window_ui_fingerprint_sha256": projection.get(
                "window_ui_fingerprint_sha256"
            ),
        },
        "plan": {
            "active_task_id": exact_task_id,
            "canonical_plan_sha256": goal_projection_after[
                "canonical_plan_sha256"
            ],
            "executable_projection_sha256": goal_projection_after[
                "projection_sha256"
            ],
        },
        "repository_identity_unchanged": repository_unchanged,
        "pointer_before": pointer_before,
        "pointer_after": pointer_after,
        "candidate_created": False,
        "pending_hil_mutated": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "git_mutated": False,
        "plan_task_advanced": False,
        "recorded_at": formula_event["recorded_at"],
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
    receipt = {**receipt_body, "receipt_sha256": receipt_sha256}
    receipt_path = (
        service.store.project_root(project_id)
        / "receipts"
        / "delta-exit"
        / f"{receipt_sha256.lower()}.json"
    )
    _write_immutable_receipt(receipt_path, receipt)
    return {
        "status": "PASS",
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "raw_plan_returned": False,
        "raw_pv_returned": False,
        "raw_chat_lineage_returned": False,
    }
