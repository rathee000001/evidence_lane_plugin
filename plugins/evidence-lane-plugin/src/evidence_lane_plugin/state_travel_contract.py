"""Public-safe contracts for exact unfinished-work State Travel."""

from __future__ import annotations

import re
from typing import Any

from .errors import require

_PROFILE_ALIASES = {
    "model": "model",
    "model_id": "model",
    "submodel": "submodel",
    "model_variant": "submodel",
    "reasoning_effort": "reasoning_effort",
    "reasoning_speed": "reasoning_speed",
    "speed": "reasoning_speed",
    "service_tier": "service_tier",
}

_CODEX_REQUIRED_PROFILE_FIELDS = (
    "model",
    "submodel",
    "reasoning_effort",
    "reasoning_speed",
)

_HOST_CONTINUITY_ZERO_COUNTERS = (
    "app_restart_count",
    "renderer_reload_count",
    "ui_freeze_count",
    "unexpected_navigation_count",
    "unexpected_task_activation_count",
    "background_agent_activation_count",
    "unbounded_thread_hydration_count",
    "collaboration_overlay_hydration_count",
)

_BOUNDED_DESTINATION_HYDRATION_MODE = "BOUNDED_HANDOFF_ENVELOPE_ONLY"
_PLAN_PROJECTION_SOURCE = "CANONICAL_PLAN_LANE_NOT_THREAD_HISTORY"
_DIRECT_FORCE_ROUTE = "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK"
_DIRECT_FORCE_CONFIRMATION = "DIRECT_FORCE_SAME_WORKTREE_STATE_TRAVEL"
_CODEX_TASK_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")

_TASK_STATUSES = {
    "COMPLETED": "COMPLETED",
    "COMPLETE": "COMPLETED",
    "DONE": "COMPLETED",
    "ACCEPTED": "COMPLETED",
    "IN_PROGRESS": "IN_PROGRESS",
    "IN PROGRESS": "IN_PROGRESS",
    "ACTIVE": "IN_PROGRESS",
    "PENDING": "PENDING",
    "QUEUED": "PENDING",
}

_PLAN_METADATA_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _direct_text(value: Any, *, field: str, max_length: int = 512) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact) and len(exact) <= max_length,
        "DIRECT_STATE_TRAVEL_BINDING_FIELD_REQUIRED",
        "Direct/forced State Travel requires every exact binding field.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _direct_task_identity(value: Any, *, field: str) -> dict[str, str]:
    require(
        isinstance(value, dict),
        "DIRECT_STATE_TRAVEL_TASK_IDENTITY_REQUIRED",
        "Direct/forced State Travel requires structured Codex task identities.",
        status="BLOCKED",
        field=field,
    )
    task_id = _direct_text(value.get("task_id"), field=f"{field}.task_id").lower()
    require(
        _CODEX_TASK_ID_RE.fullmatch(task_id) is not None,
        "DIRECT_STATE_TRAVEL_TASK_UUID_INVALID",
        "A direct/forced State Travel task identity is not an exact Codex UUID.",
        status="MISMATCH",
        field=field,
    )
    deep_link = _direct_text(
        value.get("deep_link"),
        field=f"{field}.deep_link",
    )
    require(
        deep_link == f"codex://threads/{task_id}",
        "DIRECT_STATE_TRAVEL_TASK_DEEP_LINK_MISMATCH",
        "The Codex task UUID and deep link do not bind the same task.",
        status="MISMATCH",
        field=field,
    )
    return {"task_id": task_id, "deep_link": deep_link}


def _direct_sha256(value: Any, *, field: str) -> str:
    exact = _direct_text(value, field=field, max_length=64).upper()
    require(
        _SHA256_RE.fullmatch(exact) is not None,
        "DIRECT_STATE_TRAVEL_SHA256_INVALID",
        "A direct/forced State Travel identity is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _direct_positive_int(value: Any, *, field: str, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    require(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum,
        "DIRECT_STATE_TRAVEL_INTEGER_INVALID",
        "A direct/forced State Travel numeric binding is invalid.",
        status="MISMATCH",
        field=field,
    )
    return int(value)


def normalize_direct_forced_same_worktree_binding(value: Any) -> dict[str, Any]:
    """Normalize the fresh-task, no-seal direct State Travel entry contract.

    This contract deliberately does not accept a handoff ID.  It binds one
    authoritative work source, one already-attached runtime donor, and one
    genuinely new destination task.  Exact live identities are verified by the
    session layer before the destination runtime binding is written.
    """

    require(
        isinstance(value, dict),
        "DIRECT_STATE_TRAVEL_BINDING_REQUIRED",
        "Direct/forced same-worktree State Travel requires one exact binding.",
        status="BLOCKED",
    )
    binding = dict(value)
    require(
        binding.get("schema")
        == "evidence-lane.direct-forced-same-worktree-entry.v1"
        and binding.get("route") == _DIRECT_FORCE_ROUTE
        and binding.get("confirmation") == _DIRECT_FORCE_CONFIRMATION,
        "DIRECT_STATE_TRAVEL_ROUTE_CONFIRMATION_MISMATCH",
        "The separately named direct/forced route and confirmation token are required.",
        status="MISMATCH",
    )
    request_nonce = _direct_text(
        binding.get("request_nonce"),
        field="request_nonce",
        max_length=128,
    )
    source = _direct_task_identity(
        binding.get("authoritative_source"),
        field="authoritative_source",
    )
    donor = _direct_task_identity(
        binding.get("runtime_attachment_donor"),
        field="runtime_attachment_donor",
    )
    destination_raw = binding.get("destination")
    destination = _direct_task_identity(
        destination_raw,
        field="destination",
    )
    require(
        isinstance(destination_raw, dict),
        "DIRECT_STATE_TRAVEL_DESTINATION_REQUIRED",
        "The fresh destination creation binding is required.",
        status="BLOCKED",
    )
    require(
        len({source["task_id"], donor["task_id"], destination["task_id"]}) == 3,
        "DIRECT_STATE_TRAVEL_TASK_ROLE_COLLISION",
        "Source authority, runtime donor, and fresh destination must be distinct tasks.",
        status="MISMATCH",
    )
    destination_title = _direct_text(
        destination_raw.get("title"),
        field="destination.title",
        max_length=256,
    )
    destination_project_id = _direct_text(
        destination_raw.get("project_id"),
        field="destination.project_id",
        max_length=128,
    )
    workspace_path = _direct_text(
        destination_raw.get("workspace_path"),
        field="destination.workspace_path",
        max_length=1024,
    )
    require(
        destination_raw.get("creation_kind")
        == "FRESH_NATIVE_CODEX_LOCAL_PROJECT_TASK"
        and destination_raw.get("fresh_local_task") is True
        and destination_raw.get("fork") is False
        and destination_raw.get("continued_from_chat") is False,
        "DIRECT_STATE_TRAVEL_FRESH_TASK_PROOF_REQUIRED",
        "The destination must be a genuinely new native local-project task, never a fork or Continued from chat.",
        status="MISMATCH",
    )

    sole_writer = binding.get("sole_writer")
    require(
        isinstance(sole_writer, dict)
        and sole_writer.get("policy") == "SOLE_WRITER"
        and sole_writer.get("concurrent_writer_count") == 1,
        "DIRECT_STATE_TRAVEL_SOLE_WRITER_REQUIRED",
        "Direct same-worktree entry requires one exact sole writer.",
        status="MISMATCH",
    )
    writer_id = _direct_text(
        sole_writer.get("writer_id"),
        field="sole_writer.writer_id",
        max_length=256,
    )

    sealed = binding.get("sealed_transport")
    require(
        isinstance(sealed, dict)
        and sealed.get("prepare_called") is False
        and sealed.get("resume_called") is False
        and sealed.get("transport_envelope_created") is False
        and sealed.get("transport_envelope_consumed") is False
        and sealed.get("eligible_fresh_handoff_exists") is False,
        "DIRECT_STATE_TRAVEL_SEALED_ROUTE_FORBIDDEN",
        "The direct route cannot create, consume, or fall back to a sealed handoff.",
        status="MISMATCH",
    )

    host_context = binding.get("host_context")
    require(
        isinstance(host_context, dict),
        "DIRECT_STATE_TRAVEL_HOST_CONTEXT_REQUIRED",
        "The destination must supply its bounded native host context.",
        status="BLOCKED",
    )
    current_host = _direct_task_identity(
        {
            "task_id": host_context.get("current_task_id"),
            "deep_link": host_context.get("current_task_deep_link"),
        },
        field="host_context.current_task",
    )
    process_id = _direct_text(
        host_context.get("host_process_instance_id"),
        field="host_context.host_process_instance_id",
        max_length=256,
    )
    require(
        current_host == destination
        and host_context.get("current_task_title") == destination_title
        and host_context.get("thread_hydration_mode")
        == "BOUNDED_AUTHORITY_AND_PLAN_SQLITE_ONLY"
        and host_context.get("full_thread_history_requested") is False
        and host_context.get("task7_chat_history_loaded_as_authority") is False
        and host_context.get("collaboration_overlay_active") is False,
        "DIRECT_STATE_TRAVEL_HOST_TASK_BINDING_MISMATCH",
        "The current native host surface does not prove the exact fresh destination boundary.",
        status="MISMATCH",
    )

    expected = binding.get("expected")
    require(
        isinstance(expected, dict),
        "DIRECT_STATE_TRAVEL_EXPECTED_IDENTITY_REQUIRED",
        "Direct same-worktree entry requires exact expected live identities.",
        status="BLOCKED",
    )
    pointer = expected.get("pointer")
    source_expected = expected.get("source")
    prebootstrap = expected.get("prebootstrap_source")
    plan = expected.get("plan")
    plugin = expected.get("plugin")
    runtime = expected.get("runtime")
    profile = expected.get("execution_profile")
    require(
        all(isinstance(item, dict) for item in (
            pointer,
            source_expected,
            prebootstrap,
            plan,
            plugin,
            runtime,
            profile,
        )),
        "DIRECT_STATE_TRAVEL_EXPECTED_SECTION_REQUIRED",
        "Pointer, source, pre-bootstrap source, Plan, plugin, runtime, and execution-profile identities are all required.",
        status="BLOCKED",
    )
    pointer = dict(pointer)
    source_expected = dict(source_expected)
    prebootstrap = dict(prebootstrap)
    plan = dict(plan)
    plugin = dict(plugin)
    runtime = dict(runtime)
    profile = execution_profile_from_context({"execution_profile": profile})
    require_unfinished_execution_profile(profile, host_kind="CODEX_DESKTOP")

    normalized_pointer = {
        "accepted_pv": _direct_text(pointer.get("accepted_pv"), field="expected.pointer.accepted_pv", max_length=32),
        "generation": _direct_positive_int(pointer.get("generation"), field="expected.pointer.generation", allow_zero=True),
        "pointer_sha256": _direct_sha256(pointer.get("pointer_sha256"), field="expected.pointer.pointer_sha256"),
    }
    normalized_source: dict[str, Any] = {
        "branch": _direct_text(source_expected.get("branch"), field="expected.source.branch"),
        "commit_sha": _direct_text(source_expected.get("commit_sha"), field="expected.source.commit_sha", max_length=40).lower(),
        "tree_sha": _direct_text(source_expected.get("tree_sha"), field="expected.source.tree_sha", max_length=40).lower(),
        "worktree_sha256": _direct_sha256(source_expected.get("worktree_sha256"), field="expected.source.worktree_sha256"),
        "status_sha256": _direct_sha256(source_expected.get("status_sha256"), field="expected.source.status_sha256"),
        "tracked_diff_sha256": _direct_sha256(source_expected.get("tracked_diff_sha256"), field="expected.source.tracked_diff_sha256"),
        "dirty_path_set_sha256": _direct_sha256(source_expected.get("dirty_path_set_sha256"), field="expected.source.dirty_path_set_sha256"),
        "dirty_content_sha256": _direct_sha256(source_expected.get("dirty_content_sha256"), field="expected.source.dirty_content_sha256"),
        "status_record_count": _direct_positive_int(source_expected.get("status_record_count"), field="expected.source.status_record_count", allow_zero=True),
        "dirty_path_count": _direct_positive_int(source_expected.get("dirty_path_count"), field="expected.source.dirty_path_count", allow_zero=True),
    }
    normalized_prebootstrap = {
        "branch": _direct_text(prebootstrap.get("branch"), field="expected.prebootstrap_source.branch"),
        "commit_sha": _direct_text(prebootstrap.get("commit_sha"), field="expected.prebootstrap_source.commit_sha", max_length=40).lower(),
        "tree_sha": _direct_text(prebootstrap.get("tree_sha"), field="expected.prebootstrap_source.tree_sha", max_length=40).lower(),
        "worktree_sha256": _direct_sha256(prebootstrap.get("worktree_sha256"), field="expected.prebootstrap_source.worktree_sha256"),
        "status_sha256": _direct_sha256(prebootstrap.get("status_sha256"), field="expected.prebootstrap_source.status_sha256"),
        "tracked_diff_sha256": _direct_sha256(prebootstrap.get("tracked_diff_sha256"), field="expected.prebootstrap_source.tracked_diff_sha256"),
        "dirty_path_set_sha256": _direct_sha256(prebootstrap.get("dirty_path_set_sha256"), field="expected.prebootstrap_source.dirty_path_set_sha256"),
        "dirty_content_sha256": _direct_sha256(prebootstrap.get("dirty_content_sha256"), field="expected.prebootstrap_source.dirty_content_sha256"),
        "status_record_count": _direct_positive_int(prebootstrap.get("status_record_count"), field="expected.prebootstrap_source.status_record_count", allow_zero=True),
        "dirty_path_count": _direct_positive_int(prebootstrap.get("dirty_path_count"), field="expected.prebootstrap_source.dirty_path_count", allow_zero=True),
        "captured_before_authorized_route_bootstrap": prebootstrap.get("captured_before_authorized_route_bootstrap") is True,
    }
    require(
        normalized_prebootstrap["captured_before_authorized_route_bootstrap"],
        "DIRECT_STATE_TRAVEL_PREBOOTSTRAP_BASELINE_REQUIRED",
        "The direct route must retain the exact dirty baseline captured before its authorized bootstrap implementation.",
        status="MISMATCH",
    )

    plan_hash_fields = (
        "canonical_plan_sha256",
        "goal_projection_sha256",
        "history_projection_sha256",
        "snapshot_sha256",
    )
    normalized_plan: dict[str, Any] = {
        field: _direct_sha256(plan.get(field), field=f"expected.plan.{field}")
        for field in plan_hash_fields
    }
    for field in (
        "row_start",
        "row_end",
        "task_count",
        "active_row",
        "active_batch_row_start",
        "active_batch_row_end",
        "host_window_row_start",
        "host_window_row_end",
        "next_hil_row",
        "physically_final_hil_row",
    ):
        normalized_plan[field] = _direct_positive_int(
            plan.get(field),
            field=f"expected.plan.{field}",
        )
    for field in (
        "active_task_id",
        "active_batch_id",
        "active_row_commit_batch_id",
        "next_hil_task_id",
        "physically_final_hil_task_id",
    ):
        normalized_plan[field] = _direct_text(
            plan.get(field),
            field=f"expected.plan.{field}",
            max_length=256,
        )
    require(
        normalized_plan["row_start"] <= normalized_plan["active_row"] <= normalized_plan["row_end"]
        and normalized_plan["active_batch_row_start"] <= normalized_plan["active_row"] <= normalized_plan["active_batch_row_end"]
        and normalized_plan["host_window_row_start"] == normalized_plan["active_row"]
        and normalized_plan["host_window_row_end"]
        == min(normalized_plan["active_row"] + 8, normalized_plan["row_end"])
        and normalized_plan["next_hil_row"] > normalized_plan["active_row"]
        and normalized_plan["physically_final_hil_row"] == normalized_plan["row_end"],
        "DIRECT_STATE_TRAVEL_DYNAMIC_PLAN_BINDING_INVALID",
        "The direct route Plan binding must derive the ACTIVE batch, 1+9 window, next HIL, and physical-final HIL from the live ledger.",
        status="MISMATCH",
    )

    normalized_plugin = {
        "plugin_name": _direct_text(plugin.get("plugin_name"), field="expected.plugin.plugin_name"),
        "plugin_version": _direct_text(plugin.get("plugin_version"), field="expected.plugin.plugin_version"),
        "plugin_manifest_sha256": _direct_sha256(plugin.get("plugin_manifest_sha256"), field="expected.plugin.plugin_manifest_sha256"),
        "routing_manifest_sha256": _direct_sha256(plugin.get("routing_manifest_sha256"), field="expected.plugin.routing_manifest_sha256"),
        "identity_sha256": _direct_sha256(plugin.get("identity_sha256"), field="expected.plugin.identity_sha256"),
        "tool_count": _direct_positive_int(plugin.get("tool_count"), field="expected.plugin.tool_count"),
        "read_tool_count": _direct_positive_int(plugin.get("read_tool_count"), field="expected.plugin.read_tool_count", allow_zero=True),
        "write_tool_count": _direct_positive_int(plugin.get("write_tool_count"), field="expected.plugin.write_tool_count", allow_zero=True),
    }
    normalized_runtime = {
        "state": _direct_text(runtime.get("state"), field="expected.runtime.state"),
        "generation": _direct_positive_int(runtime.get("generation"), field="expected.runtime.generation", allow_zero=True),
        "attachment_donor_task_id": _direct_text(runtime.get("attachment_donor_task_id"), field="expected.runtime.attachment_donor_task_id"),
        "host_process_instance_id": _direct_text(runtime.get("host_process_instance_id"), field="expected.runtime.host_process_instance_id"),
        "hooks_mode": _direct_text(runtime.get("hooks_mode"), field="expected.runtime.hooks_mode"),
    }
    require(
        normalized_runtime["attachment_donor_task_id"] == donor["task_id"]
        and normalized_runtime["host_process_instance_id"] == process_id
        and normalized_runtime["hooks_mode"] == "OFF_UNTIL_REPAIRED",
        "DIRECT_STATE_TRAVEL_RUNTIME_DONOR_MISMATCH",
        "Runtime donor, host process, or hooks-off binding does not match the direct route.",
        status="MISMATCH",
    )

    return {
        "schema": "evidence-lane.direct-forced-same-worktree-entry.v1",
        "route": _DIRECT_FORCE_ROUTE,
        "confirmation": _DIRECT_FORCE_CONFIRMATION,
        "request_nonce": request_nonce,
        "authoritative_source": source,
        "runtime_attachment_donor": donor,
        "destination": {
            **destination,
            "title": destination_title,
            "project_id": destination_project_id,
            "workspace_path": workspace_path,
            "creation_kind": "FRESH_NATIVE_CODEX_LOCAL_PROJECT_TASK",
            "fresh_local_task": True,
            "fork": False,
            "continued_from_chat": False,
        },
        "sole_writer": {
            "policy": "SOLE_WRITER",
            "writer_id": writer_id,
            "concurrent_writer_count": 1,
        },
        "sealed_transport": {
            "prepare_called": False,
            "resume_called": False,
            "transport_envelope_created": False,
            "transport_envelope_consumed": False,
            "eligible_fresh_handoff_exists": False,
        },
        "host_context": {
            "current_task_id": destination["task_id"],
            "current_task_deep_link": destination["deep_link"],
            "current_task_title": destination_title,
            "host_process_instance_id": process_id,
            "thread_hydration_mode": "BOUNDED_AUTHORITY_AND_PLAN_SQLITE_ONLY",
            "full_thread_history_requested": False,
            "task7_chat_history_loaded_as_authority": False,
            "collaboration_overlay_active": False,
        },
        "expected": {
            "pointer": normalized_pointer,
            "source": normalized_source,
            "prebootstrap_source": normalized_prebootstrap,
            "plan": normalized_plan,
            "plugin": normalized_plugin,
            "runtime": normalized_runtime,
            "execution_profile": profile,
        },
    }


def _plan_metadata_id(value: Any, *, field: str, position: int) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact)
        and len(exact) <= 128
        and all(character in _PLAN_METADATA_ID_CHARS for character in exact),
        "STATE_TRAVEL_TASK_METADATA_INVALID",
        "State Travel Plan metadata must use bounded public-safe identifiers.",
        status="BLOCKED",
        position=position,
        field=field,
    )
    return exact


def execution_profile_from_context(context: dict[str, Any] | None) -> dict[str, str]:
    """Extract only non-secret host execution-selector fields."""

    raw_context = context or {}
    nested = raw_context.get("execution_profile")
    source = nested if isinstance(nested, dict) else raw_context
    result: dict[str, str] = {}
    for source_key, target_key in _PROFILE_ALIASES.items():
        if source_key not in source or source[source_key] is None:
            continue
        value = source[source_key]
        require(
            isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= 128,
            "STATE_TRAVEL_EXECUTION_PROFILE_INVALID",
            "Execution-profile values must be non-empty public-safe strings.",
            status="BLOCKED",
            field=source_key,
        )
        normalized = value.strip()
        existing = result.get(target_key)
        require(
            existing in {None, normalized},
            "STATE_TRAVEL_EXECUTION_PROFILE_ALIAS_CONFLICT",
            "Two execution-profile aliases bind different values.",
            status="MISMATCH",
            field=target_key,
        )
        result[target_key] = normalized
    return result


def require_unfinished_execution_profile(
    profile: dict[str, str],
    *,
    host_kind: str,
) -> None:
    """Fail closed when a Codex handoff cannot prove the selector profile."""

    if not host_kind.startswith("CODEX"):
        return
    missing = [field for field in _CODEX_REQUIRED_PROFILE_FIELDS if not profile.get(field)]
    require(
        not missing,
        "STATE_TRAVEL_EXECUTION_PROFILE_INCOMPLETE",
        "Unfinished Codex State Travel requires model, submodel, reasoning effort, "
        "and reasoning speed so the destination can verify the same host profile.",
        status="BLOCKED",
        missing=missing,
        host_settings_mutation_supported=False,
    )


def execution_profile_mismatches(
    expected: dict[str, str],
    actual: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    return {
        field: {"expected": value, "actual": actual.get(field)}
        for field, value in expected.items()
        if actual.get(field) != value
    }


def normalize_destination_host_continuity(
    proof: Any,
    *,
    source_task_id: str,
    source_task_deep_link: str | None,
    destination_task_id: str,
    destination_task_deep_link: str | None,
) -> dict[str, Any]:
    """Validate the no-restart, UUID-bound destination creation boundary.

    State Travel is a host task transition, never an application restart or a
    title/CWD lookup.  The host must therefore prove one uninterrupted process
    instance, one exact source/destination UUID pair, one exact initial shell,
    and no unexpected task or background-agent activation before native resume.
    Full chat-history hydration and collaboration-overlay hydration are also
    forbidden on this critical path: only the sealed bounded handoff envelope
    and canonical Plan Lane may reconstruct the destination.  A missing
    capability is a mismatch rather than permission to guess.
    """

    require(
        isinstance(proof, dict),
        "STATE_TRAVEL_HOST_CONTINUITY_FAILURE",
        "State Travel requires an explicit no-restart host-continuity proof "
        "before the one-shot resume can be consumed.",
        status="MISMATCH",
        retry_allowed=False,
        handoff_consumption_allowed=False,
        app_restart_allowed=False,
    )
    exact = dict(proof)
    process_before = str(exact.get("host_process_instance_id_before") or "").strip()
    process_after = str(exact.get("host_process_instance_id_after") or "").strip()
    incidents = exact.get("observed_incidents")
    live_title_ids = exact.get("live_canonical_title_task_ids")
    counters = {field: exact.get(field) for field in _HOST_CONTINUITY_ZERO_COUNTERS}
    counters_are_zero = all(
        isinstance(value, int) and not isinstance(value, bool) and value == 0
        for value in counters.values()
    )
    identity_matches = (
        exact.get("source_task_id") == source_task_id
        and exact.get("source_task_deep_link") == source_task_deep_link
        and exact.get("destination_task_id") == destination_task_id
        and exact.get("destination_task_deep_link") == destination_task_deep_link
        and exact.get("initial_shell_source_task_id") == source_task_id
        and exact.get("initial_shell_destination_task_id") == destination_task_id
        and exact.get("host_creation_result_task_id") == destination_task_id
        and exact.get("host_creation_result_deep_link")
        == destination_task_deep_link
    )
    require(
        exact.get("schema")
        == "evidence-lane.state-travel-host-continuity.v1"
        and exact.get("status") == "PASS"
        and identity_matches
        and bool(process_before)
        and len(process_before) <= 256
        and process_before == process_after
        and exact.get("app_restart_invoked") is False
        and exact.get("title_used_as_identity") is False
        and exact.get("cwd_used_as_identity") is False
        and exact.get("thread_hydration_mode")
        == _BOUNDED_DESTINATION_HYDRATION_MODE
        and exact.get("full_thread_history_requested") is False
        and exact.get("plan_projection_source") == _PLAN_PROJECTION_SOURCE
        and exact.get("collaboration_overlay_active") is False
        and counters_are_zero
        and incidents == []
        and live_title_ids == [destination_task_id],
        "STATE_TRAVEL_HOST_CONTINUITY_FAILURE",
        "The destination crossed a forbidden host-continuity boundary: app "
        "restart/reload/freeze, unbounded thread or collaboration-overlay "
        "hydration, unexpected task or agent activation, duplicate title "
        "identity, or source/destination shell mismatch.",
        status="MISMATCH",
        retry_allowed=False,
        handoff_consumption_allowed=False,
        app_restart_allowed=False,
        source_task_id_match=exact.get("source_task_id") == source_task_id,
        initial_shell_source_match=(
            exact.get("initial_shell_source_task_id") == source_task_id
        ),
        destination_task_id_match=(
            exact.get("destination_task_id") == destination_task_id
        ),
        initial_shell_destination_match=(
            exact.get("initial_shell_destination_task_id")
            == destination_task_id
        ),
        host_process_continuous=(
            bool(process_before) and process_before == process_after
        ),
        counters=counters,
        observed_incident_count=(
            len(incidents) if isinstance(incidents, list) else None
        ),
        live_canonical_title_task_ids=(
            live_title_ids if isinstance(live_title_ids, list) else None
        ),
    )
    return {
        "schema": "evidence-lane.state-travel-host-continuity.v1",
        "status": "PASS",
        "source_task_id": source_task_id,
        "source_task_deep_link": source_task_deep_link,
        "destination_task_id": destination_task_id,
        "destination_task_deep_link": destination_task_deep_link,
        "initial_shell_source_task_id": source_task_id,
        "initial_shell_destination_task_id": destination_task_id,
        "host_creation_result_task_id": destination_task_id,
        "host_creation_result_deep_link": destination_task_deep_link,
        "host_process_instance_id_before": process_before,
        "host_process_instance_id_after": process_after,
        "app_restart_invoked": False,
        "thread_hydration_mode": _BOUNDED_DESTINATION_HYDRATION_MODE,
        "full_thread_history_requested": False,
        "plan_projection_source": _PLAN_PROJECTION_SOURCE,
        "collaboration_overlay_active": False,
        **{field: 0 for field in _HOST_CONTINUITY_ZERO_COUNTERS},
        "observed_incidents": [],
        "live_canonical_title_task_ids": [destination_task_id],
        "title_used_as_identity": False,
        "cwd_used_as_identity": False,
        "host_process_continuity_proven": True,
        "retry_allowed_after_failure": False,
        "handoff_consumption_allowed_after_failure": False,
    }


def normalize_task_list(rows: Any) -> list[dict[str, Any]]:
    """Normalize one visible Plan Lane/host task-panel projection."""

    if rows is None:
        return []
    require(
        isinstance(rows, list) and len(rows) <= 200,
        "STATE_TRAVEL_TASK_LIST_INVALID",
        "The State Travel task list must contain no more than two hundred rows.",
        status="BLOCKED",
    )
    normalized: list[dict[str, Any]] = []
    first_number: int | None = None
    for index, row in enumerate(rows, start=1):
        require(
            isinstance(row, dict),
            "STATE_TRAVEL_TASK_ROW_INVALID",
            "Every State Travel task row must be a structured object.",
            status="BLOCKED",
            position=index,
        )
        number = row.get("number", row.get("sequence", index))
        if first_number is None and isinstance(number, int):
            first_number = number
        require(
            isinstance(number, int)
            and number >= 1
            and first_number is not None
            and number == first_number + index - 1,
            "STATE_TRAVEL_TASK_SEQUENCE_INVALID",
            "State Travel task rows must use one positive contiguous visible range.",
            status="BLOCKED",
            position=index,
            number=number,
        )
        text = row.get("step", row.get("requested_outcome", row.get("title")))
        require(
            isinstance(text, str) and bool(text.strip()) and len(text) <= 20000,
            "STATE_TRAVEL_TASK_TEXT_INVALID",
            "Every State Travel task row requires its exact visible step text.",
            status="BLOCKED",
            position=index,
        )
        raw_status = str(row.get("status", "PENDING")).strip().upper()
        status = _TASK_STATUSES.get(raw_status)
        require(
            status is not None,
            "STATE_TRAVEL_TASK_STATUS_INVALID",
            "Task-panel rows support COMPLETED, IN_PROGRESS, or PENDING.",
            status="BLOCKED",
            position=index,
            supplied_status=raw_status,
        )
        task_id = str(row.get("task_id") or f"STEP_{index:03d}").strip()
        require(
            bool(task_id) and len(task_id) <= 128,
            "STATE_TRAVEL_TASK_ID_INVALID",
            "Every State Travel row requires a bounded stable task ID.",
            status="BLOCKED",
            position=index,
        )
        normalized_row: dict[str, Any] = {
            "number": number,
            "task_id": task_id,
            "step": text,
            "status": status,
        }
        canonical_plan_sequence = row.get(
            "canonical_plan_sequence",
            row.get("plan_sequence"),
        )
        if canonical_plan_sequence is not None:
            require(
                isinstance(canonical_plan_sequence, int)
                and canonical_plan_sequence >= index,
                "STATE_TRAVEL_CANONICAL_PLAN_SEQUENCE_INVALID",
                "A current Goal row must retain a valid canonical Plan sequence when supplied.",
                status="BLOCKED",
                position=index,
                canonical_plan_sequence=canonical_plan_sequence,
            )
            normalized_row["canonical_plan_sequence"] = canonical_plan_sequence
        panel_role = str(row.get("panel_role") or "").strip().upper()
        if panel_role:
            require(
                panel_role
                in {"STANDARD", "HIL_GATE", "PHYSICALLY_FINAL_HIL"},
                "STATE_TRAVEL_TASK_PANEL_ROLE_INVALID",
                "A State Travel row contains an unsupported persistent-panel role.",
                status="BLOCKED",
                position=index,
                panel_role=panel_role,
            )
            normalized_row["panel_role"] = panel_role
        task_classification = str(
            row.get("task_classification") or row.get("task_class") or ""
        ).strip()
        if task_classification:
            normalized_row["task_classification"] = _plan_metadata_id(
                task_classification,
                field="task_classification",
                position=index,
            )
        plan_group = str(row.get("plan_group") or "").strip()
        if plan_group:
            normalized_row["plan_group"] = _plan_metadata_id(
                plan_group,
                field="plan_group",
                position=index,
            )
        commit_batch_id = str(row.get("commit_batch_id") or "").strip()
        if commit_batch_id:
            normalized_row["commit_batch_id"] = _plan_metadata_id(
                commit_batch_id,
                field="commit_batch_id",
                position=index,
            )
        if "dependencies" in row:
            dependencies = row.get("dependencies")
            require(
                isinstance(dependencies, list)
                and len(dependencies) <= 64
                and all(
                    isinstance(value, str)
                    and bool(value.strip())
                    and len(value.strip()) <= 128
                    and all(
                        character in _PLAN_METADATA_ID_CHARS
                        for character in value.strip()
                    )
                    for value in dependencies
                ),
                "STATE_TRAVEL_TASK_DEPENDENCIES_INVALID",
                "State Travel dependencies must be bounded public-safe task IDs.",
                status="BLOCKED",
                position=index,
            )
            normalized_row["dependencies"] = list(
                dict.fromkeys(value.strip() for value in dependencies)
            )
        for metadata_field in (
            "dependency_source",
            "git_commit_stage",
            "git_commit_stage_source",
        ):
            metadata_value = str(row.get(metadata_field) or "").strip()
            if metadata_value:
                normalized_row[metadata_field] = _plan_metadata_id(
                    metadata_value,
                    field=metadata_field,
                    position=index,
                )
        visible_label = row.get("visible_label")
        if visible_label is not None:
            require(
                isinstance(visible_label, str)
                and bool(visible_label.strip())
                and len(visible_label) <= 25000,
                "STATE_TRAVEL_TASK_VISIBLE_LABEL_INVALID",
                "A State Travel visible label must be bounded non-empty text.",
                status="BLOCKED",
                position=index,
            )
            normalized_row["visible_label"] = visible_label
        raw_steers = row.get("steer_deltas")
        if raw_steers:
            require(
                isinstance(raw_steers, list) and len(raw_steers) <= 500,
                "STATE_TRAVEL_TASK_STEERS_INVALID",
                "A task-panel row contains an invalid steer Delta list.",
                status="BLOCKED",
                position=index,
            )
            normalized_steers: list[dict[str, Any]] = []
            for steer_position, steer in enumerate(raw_steers, start=1):
                require(
                    isinstance(steer, dict),
                    "STATE_TRAVEL_TASK_STEER_INVALID",
                    "Every task-panel steer Delta must be a structured row.",
                    status="BLOCKED",
                    position=index,
                    steer_position=steer_position,
                )
                delta_id = str(steer.get("delta_id") or "").strip()
                delta_text = steer.get("text")
                boundary = str(
                    steer.get("boundary") or "BEFORE_NEXT_HIL"
                ).strip().upper()
                require(
                    bool(delta_id)
                    and len(delta_id) <= 96
                    and isinstance(delta_text, str)
                    and bool(delta_text.strip())
                    and len(delta_text) <= 50000
                    and bool(boundary)
                    and len(boundary) <= 128,
                    "STATE_TRAVEL_TASK_STEER_CONTRACT_INVALID",
                    "A task-panel steer Delta has incomplete immutable content.",
                    status="BLOCKED",
                    position=index,
                    steer_position=steer_position,
                )
                normalized_steer = {
                    "delta_id": delta_id,
                    "text": delta_text,
                    "boundary": boundary,
                    "boundary_defaulted": bool(
                        steer.get(
                            "boundary_defaulted",
                            boundary == "BEFORE_NEXT_HIL",
                        )
                    ),
                    "classification": str(
                        steer.get("classification") or "LINKED_EXISTING_STEP"
                    ),
                    "linked_task_id": str(
                        steer.get("linked_task_id") or task_id
                    ),
                    "recorded_by": str(steer.get("recorded_by") or "UNKNOWN"),
                }
                normalized_steers.append(normalized_steer)
            normalized_row["steer_deltas"] = normalized_steers
        normalized.append(normalized_row)
    in_progress = [row for row in normalized if row["status"] == "IN_PROGRESS"]
    require(
        len(in_progress) <= 1,
        "STATE_TRAVEL_MULTIPLE_ACTIVE_STEPS",
        "The persistent task panel may contain at most one in-progress row.",
        status="BLOCKED",
        active_rows=[row["number"] for row in in_progress],
    )
    require(
        not normalized or len(in_progress) == 1,
        "STATE_TRAVEL_ACTIVE_STEP_REQUIRED",
        "A non-empty persistent task panel requires exactly one in-progress row.",
        status="BLOCKED",
        task_count=len(normalized),
    )
    return normalized

def normalize_additive_deltas(rows: Any) -> list[dict[str, Any]]:
    """Preserve exact visible steer text with its canonical HIL boundary."""

    if rows is None:
        return []
    require(
        isinstance(rows, list) and len(rows) <= 500,
        "STATE_TRAVEL_DELTA_LIST_INVALID",
        "The additive Delta list must contain no more than five hundred rows.",
        status="BLOCKED",
    )
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if isinstance(row, str):
            text = row
            linked_step = None
            boundary = "BEFORE_NEXT_HIL"
            delta_id = f"DELTA_{index:03d}"
        else:
            require(
                isinstance(row, dict),
                "STATE_TRAVEL_DELTA_ROW_INVALID",
                "Every additive Delta must be exact text or a structured row.",
                status="BLOCKED",
                position=index,
            )
            text = row.get("text", row.get("delta"))
            linked_step = row.get("linked_step")
            boundary = str(row.get("boundary") or "BEFORE_NEXT_HIL").strip().upper()
            delta_id = str(row.get("delta_id") or f"DELTA_{index:03d}").strip()
        require(
            isinstance(text, str) and bool(text.strip()) and len(text) <= 50000,
            "STATE_TRAVEL_DELTA_TEXT_INVALID",
            "Every additive Delta requires its exact visible text.",
            status="BLOCKED",
            position=index,
        )
        require(
            bool(boundary) and len(boundary) <= 128,
            "STATE_TRAVEL_DELTA_BOUNDARY_INVALID",
            "A Delta boundary must be a bounded visible label.",
            status="BLOCKED",
            position=index,
        )
        if linked_step is not None:
            require(
                isinstance(linked_step, int) and linked_step >= 1,
                "STATE_TRAVEL_DELTA_LINK_INVALID",
                "A linked Delta must name a positive task-panel row.",
                status="BLOCKED",
                position=index,
            )
        normalized.append(
            {
                "delta_id": delta_id,
                "text": text,
                "linked_step": linked_step,
                "boundary": boundary,
                "defaulted_to_pre_hil": boundary == "BEFORE_NEXT_HIL",
            }
        )
    delta_ids = [row["delta_id"] for row in normalized]
    require(
        len(delta_ids) == len(set(delta_ids)),
        "STATE_TRAVEL_DELTA_ID_DUPLICATE",
        "A State Travel handoff may seal each additive Delta ID only once.",
        status="BLOCKED",
        delta_ids=delta_ids,
    )
    return normalized


def additive_deltas_from_task_list(
    task_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project every persisted Plan Lane steer into the State Travel Delta seal."""

    projected: list[dict[str, Any]] = []
    for task in task_list:
        for steer in task.get("steer_deltas", []):
            projected.append(
                {
                    "delta_id": steer["delta_id"],
                    "text": steer["text"],
                    "linked_step": task["number"],
                    "boundary": steer["boundary"],
                }
            )
    return normalize_additive_deltas(projected)
