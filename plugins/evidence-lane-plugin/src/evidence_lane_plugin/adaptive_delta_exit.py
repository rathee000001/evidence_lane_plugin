"""Replay-safe universal per-Delta adaptive exit orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .adaptive_delta_entry import run_adaptive_delta_entry
from .authority_support import refresh_delta_exit_authority_supports
from .errors import require
from .git_adapter import inspect_repository
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
from .hook_contract import HOOK_EVENT_NAMES
from .host_plan_rehydration import prepare_host_plan_rehydration
from .internal_sdk import build_live_local_sdk_context
from .lanes import CANONICAL_LANE_IDS
from .live_authority_query import query_live_authorities

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
_EXECUTABLE_DELTA_ENTRY_FORMULA_SCHEMA = (
    "evidence-lane.executable-delta-entry-formula.v2"
)
_PENDING_CANDIDATE_PRESERVATION_SCHEMA = (
    "evidence-lane.pending-candidate-delta-exit-preservation.v1"
)
_PENDING_CANDIDATE_PRESERVATION_CONFIRMATION = (
    "PRESERVE_PENDING_CANDIDATE_DURING_DELTA_EXIT"
)
_ENTRY_FORMULA_REPAIR_SCHEMA = (
    "evidence-lane.legacy-entry-formula-delta-exit-repair.v1"
)
_ENTRY_FORMULA_REPAIR_CONFIRMATION = (
    "REPAIR_LEGACY_ENTRY_FORMULA_IN_PLACE"
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


def _candidate_snapshot(
    service: Any,
    *,
    project_id: str,
    session: Any,
) -> dict[str, Any]:
    """Seal the live proposal identity without opening accepted storage."""

    candidate_id = str(session.candidate_id or "").strip()
    if not candidate_id:
        return {
            "status": "NO_PENDING_CANDIDATE",
            "candidate_id": None,
            "session_state": session.state.value,
            "candidate_overlay_receipt_file_sha256": None,
            "pending_hil": False,
        }
    require(
        session.state.value.endswith("_CANDIDATE"),
        "ADAPTIVE_DELTA_EXIT_CANDIDATE_STATE_MISMATCH",
        "A pending proposal must remain bound to its candidate lifecycle state.",
        status="MISMATCH",
        candidate_id=candidate_id,
        state=session.state.value,
    )
    receipt_path = service.store._candidate_overlay_receipt_path(
        project_id, candidate_id
    )
    require(
        receipt_path.is_file(),
        "ADAPTIVE_DELTA_EXIT_CANDIDATE_RECEIPT_MISSING",
        "The pending live-root proposal receipt is unavailable.",
        status="MISMATCH",
        candidate_id=candidate_id,
    )
    return {
        "status": "PENDING_CANDIDATE_PRESERVED",
        "candidate_id": candidate_id,
        "session_state": session.state.value,
        "candidate_overlay_receipt_file_sha256": sha256_file(receipt_path),
        "pending_hil": True,
    }


def _pending_candidate_preservation(
    service: Any,
    *,
    project_id: str,
    session: Any,
    request: object,
) -> dict[str, Any]:
    snapshot = _candidate_snapshot(
        service,
        project_id=project_id,
        session=session,
    )
    if snapshot["candidate_id"] is None:
        require(
            request is None or request is False,
            "ADAPTIVE_DELTA_EXIT_CANDIDATE_PRESERVATION_UNEXPECTED",
            "A candidate-preservation contract was supplied without a pending candidate.",
            status="MISMATCH",
        )
        return snapshot
    require(
        isinstance(request, dict)
        and request.get("schema") == _PENDING_CANDIDATE_PRESERVATION_SCHEMA
        and request.get("confirmation")
        == _PENDING_CANDIDATE_PRESERVATION_CONFIRMATION
        and str(request.get("candidate_id") or "").strip()
        == snapshot["candidate_id"]
        and bool(str(request.get("reason") or "").strip()),
        "ADAPTIVE_DELTA_EXIT_CANDIDATE_PRESERVATION_REQUIRED",
        "Adaptive Delta exit requires an exact preserve-in-place contract for the pending proposal.",
        status="BLOCKED",
        candidate_id=snapshot["candidate_id"],
    )
    validated_request = cast(dict[str, Any], request)
    return {
        **snapshot,
        "schema": _PENDING_CANDIDATE_PRESERVATION_SCHEMA,
        "confirmation": _PENDING_CANDIDATE_PRESERVATION_CONFIRMATION,
        "reason": str(validated_request["reason"]).strip(),
        "candidate_cleared": False,
        "candidate_rebuilt": False,
        "candidate_renamed": False,
    }


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


def _validated_entry_formula_event(
    event: Mapping[str, Any],
    *,
    expected_formula_sha256: str,
) -> dict[str, Any]:
    formula = event.get("formula")
    require(
        event.get("event_kind") in {"ENTRY_FORMULA", "MUTATION"}
        and event.get("formula_sha256") == expected_formula_sha256
        and isinstance(formula, dict)
        and formula.get("schema") == _EXECUTABLE_DELTA_ENTRY_FORMULA_SCHEMA
        and formula.get("source_work_authorized") is True
        and formula.get("public_action_sdk_separate") is True
        and formula.get("env_uop_ai_action_plane_separate") is True,
        "ADAPTIVE_DELTA_EXIT_EXECUTABLE_ENTRY_REQUIRED",
        "Delta exit requires the exact open executable ENV/UOP entry formula.",
        status="MISMATCH",
    )
    formula = cast(dict[str, Any], formula)
    execution = formula.get("env_uop_runtime_execution")
    mathematical = formula.get("mathematical_execution")
    require(
        isinstance(execution, dict)
        and execution.get("status") == "PASS"
        and execution.get("plane_role")
        == "INTERNAL_AI_ACTION_PLANE_BETWEEN_SQLITE_AND_WORK"
        and execution.get("counted_as_public_action") is False
        and isinstance(execution.get("compiled_formula"), dict)
        and bool(execution["compiled_formula"].get("compiled_formula_sha256"))
        and isinstance(execution.get("operator_route_receipts"), list)
        and execution.get("operator_route_count")
        == len(execution["operator_route_receipts"])
        and isinstance(mathematical, dict)
        and mathematical.get("status") == "PASS"
        and mathematical.get("evaluated_result") is True
        and mathematical.get("null_execution") is False,
        "ADAPTIVE_DELTA_EXIT_ENV_UOP_ENTRY_INVALID",
        "The open entry formula did not complete locked SQLite/MMD compilation and routing.",
        status="MISMATCH",
    )
    execution = cast(dict[str, Any], execution)
    mathematical = cast(dict[str, Any], mathematical)
    compiled_formula = cast(dict[str, Any], execution["compiled_formula"])
    return {
        "event_id": event.get("event_id"),
        "event_kind": event.get("event_kind"),
        "formula_sha256": expected_formula_sha256,
        "env_uop_action_plane_receipt_sha256": execution.get("receipt_sha256"),
        "compiled_formula_sha256": compiled_formula.get("compiled_formula_sha256"),
        "operator_route_count": execution.get("operator_route_count"),
        "mathematical_execution_receipt_sha256": mathematical.get(
            "receipt_sha256"
        ),
        "source_work_authorized": True,
    }


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
            "model_visible_schema_sha256",
            "public_action_matrix_sha256",
        ):
            exact[field] = _required_sha256(
                exact.get(field),
                field=f"install_disposition.{field}",
            )
        require(
            str(exact.get("installed_host_status") or "").strip().upper()
            == "PASS"
            and str(
                exact.get("exact_task_reattachment_status") or ""
            ).strip().upper()
            == "PASS",
            "ADAPTIVE_DELTA_EXIT_INSTALLED_HOST_PROOF_REQUIRED",
            "A verified local install requires installed-host schema/action proof "
            "and exact-task reattachment; source tests cannot substitute for it.",
            status="BLOCKED",
        )
        exact["installed_host_status"] = "PASS"
        exact["exact_task_reattachment_status"] = "PASS"
        exact["installed_public_behavior_claim_status"] = "PASS"
        exact["install_performed"] = True
    else:
        require(
            bool(str(exact.get("reason") or "").strip()),
            "ADAPTIVE_DELTA_EXIT_NON_CODE_REASON_REQUIRED",
            "A non-code Delta classification requires its exact reason.",
            status="BLOCKED",
        )
        exact["install_performed"] = False
    local_install_attempt_count = exact.get(
        "local_install_attempt_count",
        1 if exact["install_performed"] else 0,
    )
    full_regression_required = bool(exact.get("full_regression_required", False))
    full_regression_run_count = exact.get(
        "full_regression_run_count",
        0,
    )
    require(
        isinstance(local_install_attempt_count, int)
        and not isinstance(local_install_attempt_count, bool)
        and isinstance(full_regression_run_count, int)
        and not isinstance(full_regression_run_count, bool)
        and local_install_attempt_count >= 0
        and full_regression_run_count >= 0,
        "ADAPTIVE_DELTA_EXIT_CADENCE_COUNT_INVALID",
        "Regression and install cadence counts must be exact non-negative integers.",
        status="BLOCKED",
    )
    git_stage = str(active.get("git_commit_stage") or "NO_COMMIT").strip().upper()
    git_commit_row = git_stage not in {"", "NO_COMMIT", "LOCAL_PREVIEW_ONLY"}
    local_install_max = 3 if git_commit_row else 2
    require(
        (
            exact["install_performed"]
            and 1 <= local_install_attempt_count <= local_install_max
        )
        or (not exact["install_performed"] and local_install_attempt_count == 0),
        "ADAPTIVE_DELTA_EXIT_LOCAL_INSTALL_CADENCE_EXCEEDED",
        "The Delta exceeded its row-scoped local-install cap or claimed an install without a counted attempt.",
        status="BLOCKED",
        local_install_attempt_count=local_install_attempt_count,
        local_install_max=local_install_max,
        git_commit_row=git_commit_row,
    )
    require(
        (
            full_regression_required
            and 1 <= full_regression_run_count <= 2
        )
        or (not full_regression_required and full_regression_run_count == 0),
        "ADAPTIVE_DELTA_EXIT_FULL_REGRESSION_CADENCE_EXCEEDED",
        "A full regression is row-scoped, runs only when declared, and may not loop.",
        status="BLOCKED",
        full_regression_required=full_regression_required,
        full_regression_run_count=full_regression_run_count,
    )
    exact["local_install_attempt_count"] = local_install_attempt_count
    exact["local_install_max"] = local_install_max
    exact["git_commit_row"] = git_commit_row
    exact["full_regression_required"] = full_regression_required
    exact["full_regression_run_count"] = full_regression_run_count
    exact["regression_repeated_per_linked_steer"] = False
    if not exact["install_performed"]:
        exact["installed_public_behavior_claim_status"] = "NOT_CLAIMED"
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


def _refresh_connector_brain(service: Any, *, project_id: str) -> dict[str, Any]:
    """Validate and seal the project connector brain after intelligence refresh."""

    catalog = service.connector_plugin_catalog(project_id)
    database_path = (
        service.store.project_root(project_id)
        / "connector_brain"
        / "connector-brain.sqlite"
    )
    require(
        catalog.get("status") == "PASS"
        and catalog.get("integrity") == ["ok"]
        and not catalog.get("foreign_key_errors")
        and database_path.is_file(),
        "ADAPTIVE_DELTA_EXIT_CONNECTOR_BRAIN_REFRESH_FAILED",
        "Delta exit requires an intact refreshed connector brain.",
        status="FAIL",
    )
    body = {
        "schema": "evidence-lane.delta-exit-connector-brain-refresh.v1",
        "status": "PASS",
        "project_id": project_id,
        "database_sha256": sha256_file(database_path),
        "catalog_sha256": sha256_bytes(canonical_json_bytes(catalog)),
        "active_count": int(catalog.get("active_count") or 0),
        "routable_count": int(catalog.get("routable_count") or 0),
        "secret_values_persisted": catalog.get("secret_values_persisted"),
        "project_pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


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
    requested_prior_formula = exact_prior_formula
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
        formula.get("preexisting_candidate_correction") is None,
        "ADAPTIVE_DELTA_EXIT_CANDIDATE_CLEAR_ROUTE_OBSOLETE",
        "The historical clear-and-reopen candidate route is non-executing; preserve the pending proposal in place.",
        status="BLOCKED",
    )
    candidate_preservation = _pending_candidate_preservation(
        service,
        project_id=project_id,
        session=session_before,
        request=formula.get("preexisting_candidate_preservation"),
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
    plan_slice = service.store.plan_runtime_query(
        project_id,
        task_id=exact_task_id,
        limit=8,
    )
    formula_events = [
        row
        for row in plan_slice.get("formula_events") or []
        if row.get("task_id") == exact_task_id
        and row.get("formula_sha256") == exact_prior_formula
    ]
    require(
        len(formula_events) == 1
        and bool(str(formula_events[0].get("recorded_at") or "").strip()),
        "ADAPTIVE_DELTA_EXIT_ENTRY_FORMULA_TIMESTAMP_REQUIRED",
        "Decision-support reads require the exact open formula timestamp.",
        status="MISMATCH",
    )
    entry_formula_repair: dict[str, Any] | None = None
    stored_formula = formula_events[0].get("formula")
    stored_formula_is_executable = (
        isinstance(stored_formula, dict)
        and stored_formula.get("schema")
        == _EXECUTABLE_DELTA_ENTRY_FORMULA_SCHEMA
        and stored_formula.get("source_work_authorized") is True
        and stored_formula.get("public_action_sdk_separate") is True
        and stored_formula.get("env_uop_ai_action_plane_separate") is True
    )
    if not stored_formula_is_executable:
        repair_request = formula.get("entry_formula_repair")
        require(
            isinstance(repair_request, dict)
            and repair_request.get("schema") == _ENTRY_FORMULA_REPAIR_SCHEMA
            and repair_request.get("confirmation")
            == _ENTRY_FORMULA_REPAIR_CONFIRMATION
            and str(repair_request.get("prior_formula_sha256") or "").upper()
            == requested_prior_formula
            and str(repair_request.get("candidate_id") or "").strip()
            == str(candidate_preservation.get("candidate_id") or "").strip()
            and bool(str(repair_request.get("reason") or "").strip()),
            "ADAPTIVE_DELTA_EXIT_ENTRY_FORMULA_REPAIR_REQUIRED",
            "A legacy open formula requires one exact candidate-preserving adaptive-entry repair before exit.",
            status="BLOCKED",
            prior_formula_sha256=requested_prior_formula,
        )
        repaired = run_adaptive_delta_entry(
            service,
            project_id,
            session_id,
            actor=f"{exact_actor}_ENTRY_FORMULA_REPAIR",
        )
        repaired_receipt = cast(dict[str, Any], repaired.get("receipt") or {})
        exact_prior_formula = _required_sha256(
            repaired_receipt.get("formula_sha256"),
            field="entry_formula_repair.formula_sha256",
        )
        require(
            repaired.get("status") == "PASS"
            and repaired_receipt.get("formula_disposition")
            in {
                "LEGACY_OPEN_ENTRY_SUPERSEDED_BY_EXECUTABLE_MUTATION",
                "EXISTING_OPEN_EXECUTABLE_ENTRY_REUSED",
            },
            "ADAPTIVE_DELTA_EXIT_ENTRY_FORMULA_REPAIR_FAILED",
            "The adaptive-entry repair did not produce one executable open formula head.",
            status="FAIL",
        )
        entry_formula_repair = {
            "schema": _ENTRY_FORMULA_REPAIR_SCHEMA,
            "status": "PASS",
            "confirmation": _ENTRY_FORMULA_REPAIR_CONFIRMATION,
            "requested_prior_formula_sha256": requested_prior_formula,
            "effective_prior_formula_sha256": exact_prior_formula,
            "formula_disposition": repaired_receipt.get("formula_disposition"),
            "receipt_sha256": repaired_receipt.get("receipt_sha256"),
            "candidate_preserved": True,
            "pointer_moved": False,
        }
        plan_slice = service.store.plan_runtime_query(
            project_id,
            task_id=exact_task_id,
            limit=8,
        )
        formula_events = [
            row
            for row in plan_slice.get("formula_events") or []
            if row.get("task_id") == exact_task_id
            and row.get("formula_sha256") == exact_prior_formula
        ]
        require(
            len(formula_events) == 1,
            "ADAPTIVE_DELTA_EXIT_REPAIRED_FORMULA_HEAD_MISSING",
            "The repaired executable formula head was not projected exactly once.",
            status="MISMATCH",
        )
    entry_formula_execution = _validated_entry_formula_event(
        formula_events[0],
        expected_formula_sha256=exact_prior_formula,
    )
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
    source_authority = service._refresh_delta_source_authority(
        project_id,
        session_id,
        task_id=exact_task_id,
    )
    require(
        source_authority.get("status") == "PASS"
        and source_authority.get("task_id") == exact_task_id
        and [
            row.get("lane_id")
            for row in source_authority.get("source_planes") or []
        ]
        == ["local_code", "github_code"],
        "ADAPTIVE_DELTA_EXIT_SOURCE_REFRESH_FAILED",
        "Adaptive Delta exit requires exact Local Code refresh and preserved Git baseline receipts.",
        status="FAIL",
    )
    canonical_lane_refresh = dict(
        source_authority.get("canonical_lane_refresh") or {}
    )
    lane_reports = list(canonical_lane_refresh.get("lane_reports") or [])
    fallback_rows = list(
        canonical_lane_refresh.get("full_validation_fallbacks") or []
    )
    require(
        canonical_lane_refresh.get("authority_scope")
        == "ALL_18_CANONICAL_LANES"
        and int(canonical_lane_refresh.get("canonical_lane_count") or 0)
        == len(CANONICAL_LANE_IDS)
        and canonical_lane_refresh.get("emitted_lane_ids")
        == list(CANONICAL_LANE_IDS)
        and int(canonical_lane_refresh.get("lane_report_count") or 0)
        == len(CANONICAL_LANE_IDS)
        and [row.get("lane_id") for row in lane_reports]
        == list(CANONICAL_LANE_IDS)
        and all(
            str(row.get("reason") or "").strip()
            and row.get("reason") != "UNDECLARED"
            for row in fallback_rows
        ),
        "ADAPTIVE_DELTA_EXIT_CANONICAL_LANE_REFRESH_INVALID",
        "Adaptive Delta exit requires one ordered, explicit all-18-lane Refresh receipt.",
        status="FAIL",
    )
    intelligence = _sdk_refreshes(
        service,
        project_id=project_id,
        session_id=session_id,
        request_seed=request_seed,
    )
    connector_brain = _refresh_connector_brain(service, project_id=project_id)
    authority_supports = refresh_delta_exit_authority_supports(
        service.store.project_root(project_id)
    )
    hil_delta = bool(formula.get("hil_delta"))
    project_overlay_disposition = {
        "status": (
            "HIL_CANDIDATE_BUILD_REQUIRED"
            if hil_delta
            else "NOT_RUN_NON_HIL_DELTA"
        ),
        "hil_delta": hil_delta,
        "refresh_owner": (
            "TASK_COMPLETE_AND_REFRESH_HIL_CANDIDATE_BUILD"
            if hil_delta
            else None
        ),
        "project_overlay_refreshed_during_ordinary_delta_exit": False,
    }
    decision_query = " ".join(
        str(value or "").strip()
        for value in (
            exact_task_id,
            active_before.get("requested_outcome"),
            formula.get("target_outcome"),
            formula.get("achieved_outcome"),
        )
        if str(value or "").strip()
    )[:4096]
    live_authority_query = query_live_authorities(
        service,
        project_id=project_id,
        session_id=session_id,
        query=decision_query,
        limit=4,
        refresh_on_miss=False,
    )
    require(
        live_authority_query.get("status") in {"PASS", "EMPTY"}
        and live_authority_query.get("accepted_archive_opened") is False,
        "ADAPTIVE_DELTA_EXIT_DECISION_SUPPORT_FAILED",
        "Adaptive exit requires the shared live-root six-authority query route.",
        status="FAIL",
    )
    decision_support = list(live_authority_query["initial_reads"])
    working_sector_fallback = live_authority_query["authorities"]["sector_lanes"][
        "result"
    ]
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
        "connector_brain_refresh_receipt": connector_brain,
        "authority_support_refresh_receipt": authority_supports,
        "project_overlay_disposition": project_overlay_disposition,
        "decision_support_receipts": decision_support,
        "working_sector_fallback_receipt": working_sector_fallback,
        "plan_runtime_authority_state": "LIVE_CURRENT_EXECUTION_AUTHORITY",
        "instruction_authorities_separate": ["AGENTS.md", "MEMORY.md"],
        "source_authority_refresh_receipt": source_authority,
        "entry_formula_execution_receipt": entry_formula_execution,
        "host_window_ui_fingerprint_sha256": projection.get(
            "window_ui_fingerprint_sha256"
        ),
        "preexisting_candidate_preservation": candidate_preservation,
        "entry_formula_repair": entry_formula_repair,
        "verification_layers": {
            "source_validator_status": "PASS",
            "source_validator_scope": "IMPLEMENTATION_SOURCE_ONLY",
            "sector_refresh_status": "PASS",
            "sector_refresh_represents_current_delta": True,
            "installed_public_behavior_status": exact_install[
                "installed_public_behavior_claim_status"
            ],
            "installed_public_behavior_requires_local_package": True,
            "source_tests_substitute_for_installed_host": False,
        },
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
    candidate_after = _candidate_snapshot(
        service,
        project_id=project_id,
        session=session_after,
    )
    require(
        pointer_after == pointer_before
        and candidate_after == {
            key: candidate_preservation.get(key)
            for key in (
                "status",
                "candidate_id",
                "session_state",
                "candidate_overlay_receipt_file_sha256",
                "pending_hil",
            )
        }
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
        "requested_prior_formula_sha256": requested_prior_formula,
        "effective_prior_formula_sha256": exact_prior_formula,
        "validator_set_sha256": sha256_bytes(canonical_json_bytes(exact_validators)),
        "validator_count": len(exact_validators),
        "install_disposition": exact_install,
        "intelligence_refreshes": intelligence,
        "connector_brain_refresh": connector_brain,
        "authority_support_refresh": authority_supports,
        "project_overlay_disposition": project_overlay_disposition,
        "decision_support_receipts": decision_support,
        "working_sector_fallback_receipt": working_sector_fallback,
        "plan_runtime_authority_state": "LIVE_CURRENT_EXECUTION_AUTHORITY",
        "instruction_authorities_separate": ["AGENTS.md", "MEMORY.md"],
        "source_authority_refresh": source_authority,
        "entry_formula_execution": entry_formula_execution,
        "preexisting_candidate_preservation": candidate_preservation,
        "entry_formula_repair": entry_formula_repair,
        "verification_layers": formula_payload["verification_layers"],
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
        "candidate_preserved": candidate_after["candidate_id"] is not None,
        "candidate_cleared": False,
        "candidate_rebuilt": False,
        "candidate_renamed": False,
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
