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

_HOST_EVENT_NAMES = {
    "SessionStart": "sessionStart",
    "UserPromptSubmit": "userPromptSubmit",
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "PreCompact": "preCompact",
    "PostCompact": "postCompact",
    "Stop": "stop",
    "SessionEnd": "sessionEnd",
}
_CANONICAL_EVENT_NAMES = {value: key for key, value in _HOST_EVENT_NAMES.items()}
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


def validate_installed_hook_inventory(
    reply: Mapping[str, Any],
    *,
    plugin_selector: str,
    workspace: str | Path,
) -> dict[str, Any]:
    """Validate one warning-free installed selector from ``hooks/list``."""

    if not plugin_selector.startswith("evidence-lane-plugin@"):
        raise InstalledHookReceiptError("PLUGIN_SELECTOR_INVALID")
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
    if len(hooks) != len(HOOK_EVENT_NAMES):
        raise InstalledHookReceiptError("INSTALLED_HOOK_COUNT_MISMATCH")

    by_event: dict[str, Mapping[str, Any]] = {}
    for row in hooks:
        host_name = str(row.get("eventName") or "")
        event_name = _CANONICAL_EVENT_NAMES.get(host_name)
        if event_name is None or event_name in by_event:
            raise InstalledHookReceiptError("INSTALLED_HOOK_EVENT_INVENTORY_MISMATCH")
        key = str(row.get("key") or "")
        current_hash = str(row.get("currentHash") or "")
        source_path = str(row.get("sourcePath") or "")
        if (
            row.get("source") != "plugin"
            or row.get("isManaged") is not False
            or row.get("enabled") is not True
            or row.get("trustStatus") != "trusted"
            or row.get("handlerType") != "command"
            or not source_path
            or not key.startswith(f"{plugin_selector}:")
            or _HASH.fullmatch(current_hash) is None
        ):
            raise InstalledHookReceiptError("INSTALLED_HOOK_AUTHORITY_MISMATCH")
        by_event[event_name] = row
    if tuple(name for name in HOOK_EVENT_NAMES if name in by_event) != HOOK_EVENT_NAMES:
        raise InstalledHookReceiptError("INSTALLED_HOOK_EVENT_INVENTORY_MISMATCH")

    records = [
        {
            "event_name": event_name,
            "host_event_name": _HOST_EVENT_NAMES[event_name],
            "hook_key": str(by_event[event_name]["key"]),
            "current_hash": str(by_event[event_name]["currentHash"]),
            "source_path_sha256": _source_path_hash(
                str(by_event[event_name]["sourcePath"])
            ),
            "enabled": True,
            "trust_status": "trusted",
        }
        for event_name in HOOK_EVENT_NAMES
    ]
    body: dict[str, Any] = {
        "schema": INSTALLED_HOOK_INVENTORY_SCHEMA,
        "status": "PASS",
        "plugin_selector": plugin_selector,
        "workspace_sha256": sha256_bytes(expected_workspace.encode("utf-8")),
        "hook_count": len(records),
        "event_order": list(HOOK_EVENT_NAMES),
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
        "hook_count": len(hook_records),
        "hooks": hook_records,
        "diagnostic_text_after_deterministic_redaction": True,
        "raw_workspace_path_included": False,
        "raw_command_or_source_path_included": False,
        "raw_local_paths_in_diagnostic_text": False,
        "private_reasoning_included": False,
    }
    body["diagnostic_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
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
    for event_name in HOOK_EVENT_NAMES:
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
    expected = {
        str(row["event_name"]): row
        for row in inventory.get("records") or []
        if isinstance(row, Mapping)
    }
    if tuple(name for name in HOOK_EVENT_NAMES if name in expected) != HOOK_EVENT_NAMES:
        raise InstalledHookReceiptError("INSTALLED_INVENTORY_RECORDS_INCOMPLETE")

    observed: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if any(
            str(key).casefold() in _FORBIDDEN_OBSERVATION_KEYS
            for key in observation
        ):
            raise InstalledHookReceiptError("RAW_OR_PRIVATE_OBSERVATION_FORBIDDEN")
        event_name = str(observation.get("event_name") or "")
        if event_name not in expected or event_name in observed:
            raise InstalledHookReceiptError("HOOK_OBSERVATION_EVENT_INVALID")
        reference = expected[event_name]
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
        observed[event_name] = record

    missing = [name for name in HOOK_EVENT_NAMES if name not in observed]
    body: dict[str, Any] = {
        "schema": INSTALLED_HOOK_INVOCATION_SCHEMA,
        "status": "PASS" if not missing else "PENDING_INSTALLED_INVOCATION",
        "inventory_receipt_sha256": inventory["inventory_receipt_sha256"],
        "plugin_selector": inventory["plugin_selector"],
        "event_order": list(HOOK_EVENT_NAMES),
        "observations": [observed[name] for name in HOOK_EVENT_NAMES if name in observed],
        "missing_events": missing,
        "observed_event_count": len(observed),
        "installed_invocation_proof_complete": not missing,
        "unobserved_events_relabelled_unavailable": False,
        "configuration_only_relabelled_as_invocation": False,
        "raw_payload_included": False,
    }
    body["invocation_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body
