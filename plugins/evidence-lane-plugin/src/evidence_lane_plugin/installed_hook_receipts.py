"""Fail-closed receipts for Codex-installed lifecycle hooks.

Configuration validation proves what the package declares.  This module is
deliberately separate: it validates the host's ``hooks/list`` readback and
correlates later host start/completion observations with those exact installed
hook keys and hashes.  A package declaration or a test run can never be
relabeled as installed-host invocation proof.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .hook_contract import HOOK_EVENT_NAMES
from .redaction import redact

INSTALLED_HOOK_INVENTORY_SCHEMA = (
    "evidence-lane.codex-installed-hook-inventory.v1"
)
INSTALLED_HOOK_INVOCATION_SCHEMA = (
    "evidence-lane.codex-installed-hook-invocation.v1"
)
HOOK_FAILURE_FAILBACK_SCHEMA = "evidence-lane.codex-hook-failure-failback.v1"
HOOK_UI_PROJECTION_SCHEMA = "evidence-lane.codex-hook-ui-projection.v1"

_HOST_EVENT_NAMES = {
    "SessionStart": "sessionStart",
    "SubagentStart": "subagentStart",
    "UserPromptSubmit": "userPromptSubmit",
    "PreToolUse": "preToolUse",
    "PermissionRequest": "permissionRequest",
    "PostToolUse": "postToolUse",
    "PreCompact": "preCompact",
    "PostCompact": "postCompact",
    "SubagentStop": "subagentStop",
    "Stop": "stop",
    "SessionEnd": "sessionEnd",
}
_CANONICAL_EVENT_NAMES = {value: key for key, value in _HOST_EVENT_NAMES.items()}
_HOST_EVENT_ORDER = tuple(_HOST_EVENT_NAMES[name] for name in HOOK_EVENT_NAMES)
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_FORBIDDEN_OBSERVATION_KEYS = {
    "credentials",
    "environment",
    "prompt",
    "raw_payload",
    "raw_secret",
    "private_reasoning",
}


class InstalledHookReceiptError(ValueError):
    """Raised when host readback or invocation evidence is not exact."""


def _redacted_text(value: Any) -> str:
    return str(redact(str(value or "")))


def _redacted_diagnostic_text(
    value: Any,
    path_aliases: Mapping[str, str],
) -> str:
    visible = str(value or "")
    for raw_path, alias in sorted(
        path_aliases.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if raw_path:
            visible = re.sub(re.escape(raw_path), alias, visible, flags=re.IGNORECASE)
    return _redacted_text(visible)


def _result_data(reply: Mapping[str, Any]) -> list[Any]:
    result = reply.get("result")
    source: Mapping[str, Any]
    if isinstance(result, Mapping):
        source = result
    else:
        source = reply
    data = source.get("data")
    if not isinstance(data, list):
        raise InstalledHookReceiptError("HOOKS_LIST_DATA_REQUIRED")
    return data


def _workspace_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _source_path_hash(value: str | Path) -> str:
    normalized = _workspace_key(value)
    return sha256_bytes(normalized.encode("utf-8"))


def _required_event_order(
    required_events: Iterable[str] | None,
) -> tuple[str, ...]:
    """Return one non-empty canonical event subset in registry order."""

    if required_events is None:
        return HOOK_EVENT_NAMES
    requested = tuple(str(value) for value in required_events)
    if not requested or len(requested) != len(set(requested)):
        raise InstalledHookReceiptError("REQUIRED_HOOK_EVENTS_INVALID")
    unknown = set(requested).difference(HOOK_EVENT_NAMES)
    if unknown:
        raise InstalledHookReceiptError("REQUIRED_HOOK_EVENTS_INVALID")
    return tuple(name for name in HOOK_EVENT_NAMES if name in requested)


def validate_installed_hook_inventory(
    reply: Mapping[str, Any],
    *,
    plugin_selector: str,
    workspace: str | Path,
    required_events: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate one warning-free installed selector from ``hooks/list``.

    The default remains the release boundary: every canonical hook must be
    enabled.  A progressive verifier may name a non-empty subset; those hooks
    must be enabled while the trusted state of unrelated hooks is preserved in
    the receipt without relabelling them as invoked.
    """

    if not plugin_selector.startswith("evidence-lane-plugin@"):
        raise InstalledHookReceiptError("PLUGIN_SELECTOR_INVALID")
    required_order = _required_event_order(required_events)
    required = set(required_order)
    expected_workspace = _workspace_key(workspace)
    entries = [
        row
        for row in _result_data(reply)
        if isinstance(row, Mapping)
        and _workspace_key(str(row.get("cwd") or "")) == expected_workspace
    ]
    if len(entries) != 1:
        raise InstalledHookReceiptError("ONE_HOOK_WORKSPACE_REQUIRED")
    entry = entries[0]
    if entry.get("errors"):
        raise InstalledHookReceiptError("INSTALLED_HOOK_ERRORS_PRESENT")
    if entry.get("warnings"):
        raise InstalledHookReceiptError("INSTALLED_HOOK_WARNINGS_PRESENT")

    all_hooks = [row for row in entry.get("hooks") or [] if isinstance(row, Mapping)]
    conflicting = {
        str(row.get("pluginId") or "")
        for row in all_hooks
        if str(row.get("pluginId") or "").startswith("evidence-lane-plugin@")
        and str(row.get("pluginId") or "") != plugin_selector
    }
    if conflicting:
        raise InstalledHookReceiptError("MULTIPLE_EVIDENCE_LANE_HOOK_SELECTORS")
    hooks = [
        row for row in all_hooks if str(row.get("pluginId") or "") == plugin_selector
    ]
    if len(hooks) < len(HOOK_EVENT_NAMES):
        raise InstalledHookReceiptError("INSTALLED_HOOK_COUNT_MISMATCH")

    by_event: dict[str, list[Mapping[str, Any]]] = {
        event_name: [] for event_name in HOOK_EVENT_NAMES
    }
    for row in hooks:
        host_name = str(row.get("eventName") or "")
        event_name = _CANONICAL_EVENT_NAMES.get(host_name)
        if event_name is None:
            raise InstalledHookReceiptError("INSTALLED_HOOK_EVENT_INVENTORY_MISMATCH")
        key = str(row.get("key") or "")
        current_hash = str(row.get("currentHash") or "")
        source_path = str(row.get("sourcePath") or "")
        if (
            row.get("source") != "plugin"
            or row.get("isManaged") is not False
            or not isinstance(row.get("enabled"), bool)
            or (event_name in required and row.get("enabled") is not True)
            or row.get("trustStatus") != "trusted"
            or row.get("handlerType") != "command"
            or not source_path
            or not key.startswith(f"{plugin_selector}:")
            or _HASH.fullmatch(current_hash) is None
        ):
            raise InstalledHookReceiptError("INSTALLED_HOOK_AUTHORITY_MISMATCH")
        by_event[event_name].append(row)
    if any(not by_event[name] for name in HOOK_EVENT_NAMES):
        raise InstalledHookReceiptError("INSTALLED_HOOK_EVENT_INVENTORY_MISMATCH")

    records: list[dict[str, Any]] = []
    event_action_inventory: list[dict[str, Any]] = []
    for event_ordinal, event_name in enumerate(HOOK_EVENT_NAMES, start=1):
        actions: list[dict[str, Any]] = []
        for action_ordinal, row in enumerate(
            sorted(by_event[event_name], key=lambda item: str(item.get("key") or "")),
            start=1,
        ):
            record: dict[str, Any] = {
                "hook_number": event_ordinal,
                "action_number": f"{event_ordinal}.{action_ordinal}",
                "event_action_ordinal": action_ordinal,
                "event_name": event_name,
                "host_event_name": _HOST_EVENT_NAMES[event_name],
                "hook_key": str(row["key"]),
                "current_hash": str(row["currentHash"]),
                "source_path_sha256": _source_path_hash(str(row["sourcePath"])),
                "enabled": bool(row["enabled"]),
                "trust_status": "trusted",
            }
            records.append(record)
            actions.append(
                {
                    "action_number": record["action_number"],
                    "event_action_ordinal": action_ordinal,
                    "hook_key_sha256": sha256_bytes(
                        record["hook_key"].encode("utf-8")
                    ),
                    "current_hash": record["current_hash"],
                }
            )
        event_action_inventory.append(
            {
                "hook_number": event_ordinal,
                "event_name": event_name,
                "display_number": f"Hook {event_ordinal}",
                "action_count": len(actions),
                "actions": actions,
            }
        )
    body: dict[str, Any] = {
        "schema": INSTALLED_HOOK_INVENTORY_SCHEMA,
        "status": "PASS",
        "plugin_selector": plugin_selector,
        "workspace_sha256": sha256_bytes(expected_workspace.encode("utf-8")),
        "hook_count": len(HOOK_EVENT_NAMES),
        "hook_count_semantics": "REGISTERED_EVENT_TYPE_COUNT",
        "handler_action_count": len(records),
        "handler_action_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "event_order": list(HOOK_EVENT_NAMES),
        "event_action_inventory": event_action_inventory,
        "event_action_inventory_sha256": sha256_bytes(
            canonical_json_bytes(event_action_inventory)
        ),
        "required_event_order": list(required_order),
        "progressive_subset": required_order != HOOK_EVENT_NAMES,
        "records": records,
        "warnings": [],
        "errors": [],
        "multiple_evidence_lane_selectors": False,
        "raw_workspace_path_included": False,
        "hook_commands_included": False,
        "raw_payload_included": False,
        "installed_invocation_claimed": False,
    }
    body["inventory_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def build_installed_hook_diagnostic_receipt(
    reply: Mapping[str, Any],
    *,
    plugin_selector: str,
    workspace: str | Path,
) -> dict[str, Any]:
    """Preserve exact host hook diagnostics after deterministic redaction."""

    expected_workspace = _workspace_key(workspace)
    entries = [
        row
        for row in _result_data(reply)
        if isinstance(row, Mapping)
        and _workspace_key(str(row.get("cwd") or "")) == expected_workspace
    ]
    if len(entries) != 1:
        raise InstalledHookReceiptError("ONE_HOOK_WORKSPACE_REQUIRED")
    entry = entries[0]
    path_aliases: dict[str, str] = {
        str(entry.get("cwd") or ""): (
            "[WORKSPACE_PATH_SHA256="
            f"{sha256_bytes(expected_workspace.encode('utf-8'))}]"
        )
    }
    for hook in entry.get("hooks") or []:
        if isinstance(hook, Mapping) and hook.get("sourcePath"):
            raw_path = str(hook["sourcePath"])
            path_aliases[raw_path] = (
                f"[HOOK_SOURCE_PATH_SHA256={_source_path_hash(raw_path)}]"
            )
    for error in entry.get("errors") or []:
        if isinstance(error, Mapping) and error.get("path"):
            raw_path = str(error["path"])
            path_aliases[raw_path] = (
                f"[HOOK_ERROR_PATH_SHA256={_source_path_hash(raw_path)}]"
            )
    warning_records = [
        {
            "message_after_redaction": _redacted_diagnostic_text(
                message,
                path_aliases,
            ),
            "message_sha256": sha256_bytes(str(message).encode("utf-8")),
        }
        for message in entry.get("warnings") or []
    ]
    error_records: list[dict[str, Any]] = []
    for error in entry.get("errors") or []:
        if not isinstance(error, Mapping):
            raise InstalledHookReceiptError("INSTALLED_HOOK_ERROR_INVALID")
        message = str(error.get("message") or "")
        path = str(error.get("path") or "")
        error_records.append(
            {
                "message_after_redaction": _redacted_diagnostic_text(
                    message,
                    path_aliases,
                ),
                "message_sha256": sha256_bytes(message.encode("utf-8")),
                "path_sha256": _source_path_hash(path) if path else None,
                "raw_path_included": False,
            }
        )
    hook_records: list[dict[str, Any]] = []
    for hook in entry.get("hooks") or []:
        if not isinstance(hook, Mapping) or hook.get("pluginId") != plugin_selector:
            continue
        command = str(hook.get("command") or "")
        status_message = str(hook.get("statusMessage") or "")
        source_path = str(hook.get("sourcePath") or "")
        hook_records.append(
            {
                "event_name": str(hook.get("eventName") or ""),
                "hook_key": str(hook.get("key") or ""),
                "current_hash": str(hook.get("currentHash") or ""),
                "enabled": hook.get("enabled"),
                "trust_status": str(hook.get("trustStatus") or ""),
                "handler_type": str(hook.get("handlerType") or ""),
                "timeout_seconds": hook.get("timeoutSec"),
                "command_sha256": (
                    sha256_bytes(command.encode("utf-8")) if command else None
                ),
                "source_path_sha256": (
                    _source_path_hash(source_path) if source_path else None
                ),
                "status_message_after_redaction": _redacted_diagnostic_text(
                    status_message,
                    path_aliases,
                ),
                "status_message_sha256": (
                    sha256_bytes(status_message.encode("utf-8"))
                    if status_message
                    else None
                ),
                "raw_command_included": False,
                "raw_source_path_included": False,
            }
        )
    numbered_records: list[dict[str, Any]] = []
    diagnostic_event_actions: list[dict[str, Any]] = []
    for event_ordinal, host_event_name in enumerate(_HOST_EVENT_ORDER, start=1):
        rows = sorted(
            (
                row
                for row in hook_records
                if row["event_name"] == host_event_name
            ),
            key=lambda row: str(row["hook_key"]),
        )
        actions: list[dict[str, Any]] = []
        for action_ordinal, row in enumerate(rows, start=1):
            numbered = {
                **row,
                "hook_number": event_ordinal,
                "action_number": f"{event_ordinal}.{action_ordinal}",
                "event_action_ordinal": action_ordinal,
            }
            numbered_records.append(numbered)
            actions.append(
                {
                    "action_number": numbered["action_number"],
                    "event_action_ordinal": action_ordinal,
                    "hook_key_sha256": sha256_bytes(
                        str(numbered["hook_key"]).encode("utf-8")
                    ),
                    "current_hash": numbered["current_hash"],
                }
            )
        diagnostic_event_actions.append(
            {
                "hook_number": event_ordinal,
                "event_name": _CANONICAL_EVENT_NAMES[host_event_name],
                "host_event_name": host_event_name,
                "display_number": f"Hook {event_ordinal}",
                "action_count": len(actions),
                "actions": actions,
            }
        )
    body: dict[str, Any] = {
        "schema": "evidence-lane.codex-installed-hook-diagnostic.v1",
        "status": (
            "PASS" if not warning_records and not error_records else "HOST_LOAD_ISSUE"
        ),
        "plugin_selector": plugin_selector,
        "workspace_sha256": sha256_bytes(expected_workspace.encode("utf-8")),
        "warning_count": len(warning_records),
        "warnings": warning_records,
        "error_count": len(error_records),
        "errors": error_records,
        "hook_count": sum(bool(row["action_count"]) for row in diagnostic_event_actions),
        "hook_count_semantics": "REGISTERED_EVENT_TYPE_COUNT",
        "handler_action_count": len(numbered_records),
        "handler_action_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "event_action_inventory": diagnostic_event_actions,
        "event_action_inventory_sha256": sha256_bytes(
            canonical_json_bytes(diagnostic_event_actions)
        ),
        "hooks": numbered_records,
        "diagnostic_text_after_deterministic_redaction": True,
        "raw_workspace_path_included": False,
        "raw_command_or_source_path_included": False,
        "raw_local_paths_in_diagnostic_text": False,
        "private_reasoning_included": False,
    }
    body["diagnostic_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def build_host_hook_ui_projection_receipt(
    diagnostic: Mapping[str, Any],
    *,
    visible_host_events: Iterable[str],
    renderer_hook_title_mode: str,
    host_build: str,
    renderer_source_sha256: str,
) -> dict[str, Any]:
    """Separate installed hook authority from a host settings projection.

    Codex owns the settings renderer.  A complete ``hooks/list`` inventory can
    therefore coexist with a host build that omits one event group or labels
    each row by index.  This receipt makes that limitation explicit; it never
    relabels a renderer omission as a missing plugin hook and never claims that
    repacking the plugin can patch a signed host bundle.
    """

    if (
        diagnostic.get("schema")
        != "evidence-lane.codex-installed-hook-diagnostic.v1"
        or diagnostic.get("status") != "PASS"
    ):
        raise InstalledHookReceiptError("PASSING_HOOK_DIAGNOSTIC_REQUIRED")
    hooks = [
        row
        for row in diagnostic.get("hooks") or []
        if isinstance(row, Mapping)
    ]
    installed_events = tuple(str(row.get("event_name") or "") for row in hooks)
    hook_keys = tuple(str(row.get("hook_key") or "") for row in hooks)
    if (
        len(hooks) < len(HOOK_EVENT_NAMES)
        or set(installed_events) != set(_HOST_EVENT_ORDER)
        or len(set(installed_events)) != len(HOOK_EVENT_NAMES)
        or len(set(hook_keys)) != len(hook_keys)
        or any(not key for key in hook_keys)
        or any(row.get("trust_status") != "trusted" for row in hooks)
    ):
        raise InstalledHookReceiptError("INSTALLED_HOOK_DIAGNOSTIC_INCOMPLETE")

    visible = tuple(str(value) for value in visible_host_events)
    if (
        len(visible) != len(set(visible))
        or not set(visible).issubset(_HOST_EVENT_ORDER)
    ):
        raise InstalledHookReceiptError("HOST_HOOK_UI_EVENT_ORDER_INVALID")
    if renderer_hook_title_mode not in {
        "HOOK_KEY_OR_STATUS_AWARE",
        "INDEX_ONLY_GENERIC",
    }:
        raise InstalledHookReceiptError("HOST_HOOK_UI_TITLE_MODE_INVALID")
    normalized_build = str(host_build or "").strip()
    normalized_renderer_sha256 = str(renderer_source_sha256 or "").upper()
    if not normalized_build:
        raise InstalledHookReceiptError("HOST_BUILD_REQUIRED")
    if not re.fullmatch(r"[A-F0-9]{64}", normalized_renderer_sha256):
        raise InstalledHookReceiptError("HOST_RENDERER_SOURCE_SHA256_INVALID")

    missing = [name for name in _HOST_EVENT_ORDER if name not in visible]
    generic_titles = renderer_hook_title_mode == "INDEX_ONLY_GENERIC"
    limited = bool(missing or generic_titles)
    body: dict[str, Any] = {
        "schema": HOOK_UI_PROJECTION_SCHEMA,
        "status": "HOST_UI_PROJECTION_LIMITED" if limited else "PASS",
        "installed_hook_diagnostic_receipt_sha256": diagnostic.get(
            "diagnostic_receipt_sha256"
        ),
        "plugin_selector": diagnostic.get("plugin_selector"),
        "installed_hook_count": len(
            {str(row.get("event_name") or "") for row in hooks}
        ),
        "installed_hook_count_semantics": "REGISTERED_EVENT_TYPE_COUNT",
        "installed_handler_action_count": len(hooks),
        "installed_handler_action_count_semantics": (
            "TOTAL_NESTED_HANDLER_ACTION_COUNT"
        ),
        "installed_host_event_order": list(_HOST_EVENT_ORDER),
        "installed_hook_keys_distinct": True,
        "installed_hook_contract_complete": True,
        "visible_host_event_order": list(visible),
        "visible_host_event_count": len(visible),
        "missing_visible_host_events": missing,
        "renderer_hook_title_mode": renderer_hook_title_mode,
        "renderer_uses_generic_index_titles": generic_titles,
        "host_build_sha256": sha256_bytes(normalized_build.encode("utf-8")),
        "renderer_source_sha256": normalized_renderer_sha256,
        "host_settings_projection_authoritative_for_plugin_inventory": False,
        "renderer_omission_relabelled_as_missing_plugin_hook": False,
        "plugin_repack_or_reinstall_expected_to_patch_signed_host_ui": False,
        "host_update_required_for_full_ui_projection": limited,
        "hook_enablement_mutated": False,
        "plugin_installation_mutated": False,
    }
    body["projection_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def build_invocation_receipt_from_codex_notifications(
    inventory: Mapping[str, Any],
    notifications: Iterable[Mapping[str, Any]],
    *,
    host_session_id: str,
) -> dict[str, Any]:
    """Seal actual Codex hook start/completion notifications.

    Only protocol-owned identity and timing fields are consumed. Hook output,
    raw source paths, thread IDs, turn IDs, and host-session IDs are never
    copied into the receipt.
    """

    if not host_session_id:
        raise InstalledHookReceiptError("HOST_SESSION_ID_REQUIRED")
    if (
        inventory.get("schema") != INSTALLED_HOOK_INVENTORY_SCHEMA
        or inventory.get("status") != "PASS"
    ):
        raise InstalledHookReceiptError("PASSING_INSTALLED_INVENTORY_REQUIRED")
    expected_by_host = {
        str(row.get("host_event_name") or ""): row
        for row in inventory.get("records") or []
        if isinstance(row, Mapping)
    }
    if set(expected_by_host) != set(_CANONICAL_EVENT_NAMES):
        raise InstalledHookReceiptError("INSTALLED_INVENTORY_RECORDS_INCOMPLETE")
    required_order = _required_event_order(inventory.get("required_event_order"))
    required_hosts = {_HOST_EVENT_NAMES[name] for name in required_order}

    started: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    for notification in notifications:
        method = str(notification.get("method") or "")
        if method not in {"hook/started", "hook/completed"}:
            continue
        params = notification.get("params")
        if not isinstance(params, Mapping):
            raise InstalledHookReceiptError("HOOK_NOTIFICATION_PARAMS_REQUIRED")
        run = params.get("run")
        if not isinstance(run, Mapping):
            raise InstalledHookReceiptError("HOOK_NOTIFICATION_RUN_REQUIRED")
        host_event = str(run.get("eventName") or "")
        reference = expected_by_host.get(host_event)
        if host_event not in required_hosts:
            continue
        run_id = str(run.get("id") or "")
        thread_id = str(params.get("threadId") or "")
        source_path = str(run.get("sourcePath") or "")
        started_at = run.get("startedAt")
        if (
            reference is None
            or not run_id
            or len(run_id) > 256
            or not thread_id
            or len(thread_id) > 256
            or run.get("source") != "plugin"
            or run.get("handlerType") != "command"
            or run.get("executionMode") not in {"sync", "async"}
            or not isinstance(started_at, int)
            or started_at < 0
            or not source_path
            or _source_path_hash(source_path)
            != reference.get("source_path_sha256")
        ):
            raise InstalledHookReceiptError("HOOK_NOTIFICATION_IDENTITY_MISMATCH")
        sanitized = {
            "run_id": run_id,
            "host_event_name": host_event,
            "event_name": _CANONICAL_EVENT_NAMES[host_event],
            "started_at": started_at,
            "thread_id_sha256": sha256_bytes(thread_id.encode("utf-8")),
            "turn_id_sha256": (
                sha256_bytes(str(params["turnId"]).encode("utf-8"))
                if params.get("turnId") is not None
                else None
            ),
            "output_sha256": sha256_bytes(
                canonical_json_bytes(run.get("entries") or [])
            ),
        }
        if method == "hook/started":
            if run.get("status") != "running" or run.get("completedAt") is not None:
                raise InstalledHookReceiptError("HOOK_STARTED_NOTIFICATION_INVALID")
            if run_id in started:
                raise InstalledHookReceiptError("HOOK_STARTED_NOTIFICATION_DUPLICATED")
            started[run_id] = sanitized
            continue
        completed_at = run.get("completedAt")
        duration_ms = run.get("durationMs")
        if (
            run.get("status") != "completed"
            or not isinstance(completed_at, int)
            or completed_at < started_at
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        ):
            raise InstalledHookReceiptError("HOOK_COMPLETED_NOTIFICATION_INVALID")
        if run_id in completed:
            raise InstalledHookReceiptError("HOOK_COMPLETED_NOTIFICATION_DUPLICATED")
        completed[run_id] = {
            **sanitized,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
        }

    paired_by_event: dict[str, list[dict[str, Any]]] = {}
    for run_id, finish in completed.items():
        start = started.get(run_id)
        if start is None:
            raise InstalledHookReceiptError("HOOK_COMPLETION_WITHOUT_START")
        if any(
            start[key] != finish[key]
            for key in (
                "host_event_name",
                "event_name",
                "started_at",
                "thread_id_sha256",
                "turn_id_sha256",
            )
        ):
            raise InstalledHookReceiptError("HOOK_NOTIFICATION_CORRELATION_MISMATCH")
        paired_by_event.setdefault(finish["event_name"], []).append(finish)

    observations: list[dict[str, Any]] = []
    for event_name in required_order:
        pairs = paired_by_event.get(event_name) or []
        if not pairs:
            continue
        selected = max(
            pairs,
            key=lambda row: (row["completed_at"], row["started_at"], row["run_id"]),
        )
        reference = expected_by_host[_HOST_EVENT_NAMES[event_name]]
        observations.append(
            {
                "event_name": event_name,
                "hook_key": reference["hook_key"],
                "current_hash": reference["current_hash"],
                "status": "COMPLETED",
                "host_started_event_id": f"{selected['run_id']}:started",
                "host_completed_event_id": f"{selected['run_id']}:completed",
                "host_session_id": host_session_id,
                "host_thread_id_sha256": selected["thread_id_sha256"],
                "host_turn_id_sha256": selected["turn_id_sha256"],
                "host_output_sha256": selected["output_sha256"],
                "duration_ms": selected["duration_ms"],
                "matching_completed_run_count": len(pairs),
            }
        )
    receipt = build_installed_hook_invocation_receipt(inventory, observations)
    receipt["source"] = "CODEX_APP_SERVER_HOOK_NOTIFICATIONS"
    receipt["raw_source_paths_included"] = False
    receipt["raw_thread_or_turn_ids_included"] = False
    receipt["raw_hook_output_included"] = False
    receipt["notification_pair_count"] = sum(len(rows) for rows in paired_by_event.values())
    receipt.pop("invocation_receipt_sha256")
    receipt["invocation_receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


def build_installed_hook_invocation_receipt(
    inventory: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Correlate real host start/completion observations with installed hooks.

    Missing event observations yield ``PENDING_INSTALLED_INVOCATION``.  They do
    not become a synthetic PASS and do not become ``HOST_CAPABILITY_UNAVAILABLE``
    merely because the event has not happened during the observation window.
    """

    if (
        inventory.get("schema") != INSTALLED_HOOK_INVENTORY_SCHEMA
        or inventory.get("status") != "PASS"
    ):
        raise InstalledHookReceiptError("PASSING_INSTALLED_INVENTORY_REQUIRED")
    expected_records = [
        row for row in inventory.get("records") or [] if isinstance(row, Mapping)
    ]
    expected_by_key = {
        str(row["hook_key"]): row for row in expected_records
    }
    expected_by_event = {
        event_name: [
            row
            for row in expected_records
            if str(row.get("event_name") or "") == event_name
        ]
        for event_name in HOOK_EVENT_NAMES
    }
    if (
        len(expected_by_key) != len(expected_records)
        or any(not expected_by_event[name] for name in HOOK_EVENT_NAMES)
    ):
        raise InstalledHookReceiptError("INSTALLED_INVENTORY_RECORDS_INCOMPLETE")

    required_order = _required_event_order(inventory.get("required_event_order"))
    required = set(required_order)
    observed: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if any(
            str(key).casefold() in _FORBIDDEN_OBSERVATION_KEYS
            for key in observation
        ):
            raise InstalledHookReceiptError("RAW_OR_PRIVATE_OBSERVATION_FORBIDDEN")
        event_name = str(observation.get("event_name") or "")
        hook_key = str(observation.get("hook_key") or "")
        if event_name not in required or hook_key in observed:
            raise InstalledHookReceiptError("HOOK_OBSERVATION_EVENT_INVALID")
        reference = expected_by_key.get(hook_key)
        if reference is None or reference.get("event_name") != event_name:
            raise InstalledHookReceiptError("HOOK_OBSERVATION_IDENTITY_MISMATCH")
        if (
            observation.get("hook_key") != reference.get("hook_key")
            or observation.get("current_hash") != reference.get("current_hash")
            or observation.get("status") != "COMPLETED"
        ):
            raise InstalledHookReceiptError("HOOK_OBSERVATION_IDENTITY_MISMATCH")
        started = str(observation.get("host_started_event_id") or "")
        completed = str(observation.get("host_completed_event_id") or "")
        host_session = str(observation.get("host_session_id") or "")
        if not started or not completed or not host_session or started == completed:
            raise InstalledHookReceiptError("HOOK_OBSERVATION_CORRELATION_REQUIRED")
        record = {
            "hook_number": reference.get("hook_number"),
            "action_number": reference.get("action_number"),
            "event_action_ordinal": reference.get("event_action_ordinal"),
            "event_name": event_name,
            "hook_key": str(reference["hook_key"]),
            "current_hash": str(reference["current_hash"]),
            "status": "COMPLETED",
            "host_started_event_id_sha256": sha256_bytes(started.encode("utf-8")),
            "host_completed_event_id_sha256": sha256_bytes(
                completed.encode("utf-8")
            ),
            "host_session_id_sha256": sha256_bytes(host_session.encode("utf-8")),
            "raw_host_event_id_included": False,
            "raw_payload_included": False,
        }
        safe_hash_fields = (
            "host_thread_id_sha256",
            "host_turn_id_sha256",
            "host_output_sha256",
        )
        for key in safe_hash_fields:
            value = observation.get(key)
            if value is not None:
                normalized = str(value).upper()
                if re.fullmatch(r"[A-F0-9]{64}", normalized) is None:
                    raise InstalledHookReceiptError("HOOK_OBSERVATION_HASH_INVALID")
                record[key] = normalized
        if "duration_ms" in observation:
            duration_ms = observation["duration_ms"]
            if not isinstance(duration_ms, int) or duration_ms < 0:
                raise InstalledHookReceiptError("HOOK_OBSERVATION_DURATION_INVALID")
            record["duration_ms"] = duration_ms
        if "matching_completed_run_count" in observation:
            matching_count = observation["matching_completed_run_count"]
            if not isinstance(matching_count, int) or matching_count < 1:
                raise InstalledHookReceiptError("HOOK_OBSERVATION_COUNT_INVALID")
            record["matching_completed_run_count"] = matching_count
        observed[hook_key] = record

    missing_action_numbers = [
        str(row.get("action_number") or "")
        for event_name in required_order
        for row in expected_by_event[event_name]
        if str(row["hook_key"]) not in observed
    ]
    missing = [
        event_name
        for event_name in required_order
        if any(
            str(row["hook_key"]) not in observed
            for row in expected_by_event[event_name]
        )
    ]
    ordered_observations = [
        observed[str(row["hook_key"])]
        for event_name in required_order
        for row in expected_by_event[event_name]
        if str(row["hook_key"]) in observed
    ]
    body: dict[str, Any] = {
        "schema": INSTALLED_HOOK_INVOCATION_SCHEMA,
        "status": "PASS" if not missing else "PENDING_INSTALLED_INVOCATION",
        "inventory_receipt_sha256": inventory["inventory_receipt_sha256"],
        "plugin_selector": inventory["plugin_selector"],
        "event_order": list(required_order),
        "observations": ordered_observations,
        "missing_events": missing,
        "missing_action_numbers": missing_action_numbers,
        "observed_event_count": len(
            {row["event_name"] for row in ordered_observations}
        ),
        "observed_handler_action_count": len(ordered_observations),
        "required_handler_action_count": sum(
            len(expected_by_event[name]) for name in required_order
        ),
        "installed_invocation_proof_complete": not missing,
        "unobserved_events_relabelled_unavailable": False,
        "configuration_only_relabelled_as_invocation": False,
        "raw_payload_included": False,
    }
    body["invocation_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def build_independent_hook_failback_request(
    inventory: Mapping[str, Any],
    *,
    event_name: str,
    failure_code: str,
) -> dict[str, Any]:
    """Build one CAS-ready native request that disables only a failed hook.

    This is a request contract, not evidence that the host write succeeded.
    The installer/runtime owner must execute it through ``config/batchWrite``
    with a fresh compare-and-swap baseline and then re-read ``hooks/list``.
    """

    if (
        inventory.get("schema") != INSTALLED_HOOK_INVENTORY_SCHEMA
        or inventory.get("status") != "PASS"
    ):
        raise InstalledHookReceiptError("PASSING_INSTALLED_INVENTORY_REQUIRED")
    if event_name not in HOOK_EVENT_NAMES:
        raise InstalledHookReceiptError("FAILED_HOOK_EVENT_INVALID")
    normalized_failure = str(failure_code or "").strip()
    if not normalized_failure or len(normalized_failure) > 256:
        raise InstalledHookReceiptError("FAILED_HOOK_CODE_INVALID")
    records = [
        dict(row)
        for row in inventory.get("records") or []
        if isinstance(row, Mapping)
    ]
    if tuple(str(row.get("event_name") or "") for row in records) != HOOK_EVENT_NAMES:
        raise InstalledHookReceiptError("INSTALLED_INVENTORY_RECORDS_INCOMPLETE")
    target = next(row for row in records if row["event_name"] == event_name)
    hook_key = str(target.get("hook_key") or "")
    current_hash = str(target.get("current_hash") or "")
    if not hook_key or _HASH.fullmatch(current_hash) is None:
        raise InstalledHookReceiptError("FAILED_HOOK_AUTHORITY_INVALID")
    body: dict[str, Any] = {
        "schema": HOOK_FAILURE_FAILBACK_SCHEMA,
        "status": "PASS",
        "action": "DISABLE_EXACT_FAILED_HOOK",
        "supported_codex_api": "config/batchWrite",
        "compare_and_swap_required": True,
        "post_write_hooks_list_readback_required": True,
        "plugin_selector": inventory.get("plugin_selector"),
        "inventory_receipt_sha256": inventory.get("inventory_receipt_sha256"),
        "event_name": event_name,
        "host_event_name": _HOST_EVENT_NAMES[event_name],
        "hook_key": hook_key,
        "current_hash": current_hash,
        "failure_code": normalized_failure,
        "config_edit": {
            "keyPath": "hooks.state",
            "mergeStrategy": "replace",
            "value": {
                str(row["hook_key"]): {
                    "trusted_hash": str(row["current_hash"]),
                    "enabled": (
                        False
                        if row["event_name"] == event_name
                        else bool(row["enabled"])
                    ),
                }
                for row in records
            },
        },
        "target_hook_enabled_after_write": False,
        "target_hook_trust_preserved": True,
        "unrelated_hook_state_mutated": False,
        "plugin_enablement_mutated": False,
        "hooks_off_compatible": True,
        "execution_claimed": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }
    body["failback_request_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body
