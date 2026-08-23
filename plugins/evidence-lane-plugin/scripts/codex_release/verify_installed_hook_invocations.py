"""Exercise trusted Evidence Lane hooks through a real Codex app-server.

The verifier is deliberately isolated. It requires a caller-provided Codex
home containing an already-installed, already-trusted plugin selector and a
separate Evidence Lane data root. A loopback-only Responses API stub requests
one read-only ``Get-Location`` tool call, returns one assistant message, and
serves one manual compaction response. No OpenAI credential or remote service
is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any, Self

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.installed_hook_receipts import (
    InstalledHookReceiptError,
    build_invocation_receipt_from_codex_notifications,
    validate_installed_hook_inventory,
)
from evidence_lane_plugin.redaction import redact

PROBE_SCHEMA = "evidence-lane.codex-installed-hook-invocation-probe.v1"
PROGRESSIVE_PROBE_SCHEMA = (
    "evidence-lane.codex-progressive-installed-hook-battle-test.v1"
)
_REQUEST_TIMEOUT_SECONDS = 45.0
_EVENT_SETTLE_SECONDS = 3.0
_CANONICAL_TO_HOST = {
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


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sse(events: Iterable[dict[str, Any]]) -> bytes:
    chunks: list[str] = []
    for event in events:
        kind = str(event["type"])
        chunks.append(f"event: {kind}\ndata: {_json_bytes(event).decode('utf-8')}\n\n")
    return "".join(chunks).encode("utf-8")


def _response_created(response_id: str) -> dict[str, Any]:
    return {"type": "response.created", "response": {"id": response_id}}


def _response_completed(response_id: str) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "usage": {
                "input_tokens": 0,
                "input_tokens_details": None,
                "output_tokens": 0,
                "output_tokens_details": None,
                "total_tokens": 0,
            },
        },
    }


def _scripted_responses() -> list[bytes]:
    return [
        _sse(
            [
                _response_created("row174-tool-response"),
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "row174-safe-tool",
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "Get-Location"}),
                    },
                },
                _response_completed("row174-tool-response"),
            ]
        ),
        _sse(
            [
                _response_created("row174-message-response"),
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "id": "row174-message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Row174 isolated hook probe complete.",
                            }
                        ],
                    },
                },
                _response_completed("row174-message-response"),
            ]
        ),
        _sse(
            [
                _response_created("row174-compact-response"),
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "compaction",
                        "encrypted_content": "ROW174_ISOLATED_COMPACTION_SUMMARY",
                    },
                },
                _response_completed("row174-compact-response"),
            ]
        ),
    ]


class _LoopbackResponsesServer:
    def __init__(self) -> None:
        self._responses = _scripted_responses()
        self._request_records: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path.rstrip("/").endswith("/models"):
                    body = _json_bytes(
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": "gpt-5.6-sol",
                                    "object": "model",
                                    "owned_by": "row174-loopback",
                                }
                            ],
                        }
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                request_body = self.rfile.read(content_length)
                owner._request_records.append(
                    {
                        "ordinal": len(owner._request_records) + 1,
                        "path_sha256": hashlib.sha256(
                            self.path.encode("utf-8")
                        ).hexdigest().upper(),
                        "body_sha256": hashlib.sha256(request_body)
                        .hexdigest()
                        .upper(),
                    }
                )
                ordinal = len(owner._request_records) - 1
                if ordinal >= len(owner._responses):
                    self.send_error(409)
                    return
                body = owner._responses[ordinal]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="row174-loopback-responses",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        if host != "127.0.0.1":
            raise RuntimeError("LOOPBACK_BINDING_REQUIRED")
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(self._request_records)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _AppServerClient:
    def __init__(
        self,
        *,
        executable: Path,
        codex_home: Path,
        data_root: Path,
        base_url: str,
    ) -> None:
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(codex_home)
        environment["EVIDENCE_LANE_DATA_ROOT"] = str(data_root)
        environment["OPENAI_API_KEY"] = "row174-loopback-only"
        # Fail closed against accidental remote traffic.  The custom provider
        # below bypasses these dead proxies only for the explicit loopback URL.
        environment["NO_PROXY"] = "127.0.0.1,localhost"
        environment["no_proxy"] = environment["NO_PROXY"]
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[key] = "http://127.0.0.1:9"
            environment[key.lower()] = environment[key]
        self._process = subprocess.Popen(
            [str(executable), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):
            self.close()
            raise RuntimeError("CODEX_APP_SERVER_STDIO_UNAVAILABLE")
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.notifications: list[dict[str, Any]] = []
        self._pending_responses: dict[int, dict[str, Any]] = {}
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                self._messages.put(value)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr_lines.append(line.rstrip())

    def send(self, value: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(_json_bytes(value).decode("utf-8") + "\n")
        self._process.stdin.flush()

    def _next_message(self, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("CODEX_APP_SERVER_CLOSED_UNEXPECTEDLY")
            try:
                value = self._messages.get(
                    timeout=min(0.25, max(0.01, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if "method" in value and "id" not in value:
                self.notifications.append(value)
                continue
            if "method" in value and "id" in value:
                raise RuntimeError("UNEXPECTED_CODEX_APP_SERVER_CLIENT_REQUEST")
            return value
        raise RuntimeError("CODEX_APP_SERVER_RESPONSE_TIMEOUT")

    def request(
        self,
        request_id: int,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
        allow_error: bool = False,
    ) -> dict[str, Any]:
        self.send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + timeout_seconds
        cached = self._pending_responses.pop(request_id, None)
        if cached is not None:
            return cached
        while time.monotonic() < deadline:
            value = self._next_message(deadline)
            response_id = value.get("id")
            if response_id == request_id:
                if value.get("error") and not allow_error:
                    raise RuntimeError(f"CODEX_REQUEST_FAILED:{method}")
                return value
            if isinstance(response_id, int):
                self._pending_responses[response_id] = value
        raise RuntimeError(f"CODEX_REQUEST_TIMEOUT:{method}")

    def settle(self, seconds: float = _EVENT_SETTLE_SECONDS) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                value = self._messages.get(
                    timeout=min(0.1, max(0.01, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if "method" in value and "id" not in value:
                self.notifications.append(value)
            elif isinstance(value.get("id"), int):
                self._pending_responses[int(value["id"])] = value

    def wait_for_notification(
        self,
        method: str,
        *,
        after_index: int = 0,
        timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        for notification in self.notifications[after_index:]:
            if notification.get("method") == method:
                return notification
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("CODEX_APP_SERVER_CLOSED_UNEXPECTEDLY")
            try:
                value = self._messages.get(
                    timeout=min(0.25, max(0.01, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if "method" in value and "id" not in value:
                self.notifications.append(value)
                if value.get("method") == method:
                    return value
            elif "method" in value and "id" in value:
                raise RuntimeError("UNEXPECTED_CODEX_APP_SERVER_CLIENT_REQUEST")
            elif isinstance(value.get("id"), int):
                self._pending_responses[int(value["id"])] = value
        raise RuntimeError(f"CODEX_NOTIFICATION_TIMEOUT:{method}")

    @property
    def stderr_sha256(self) -> str:
        return hashlib.sha256("\n".join(self._stderr_lines).encode("utf-8")).hexdigest().upper()

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)


def _thread_id(reply: dict[str, Any]) -> str:
    try:
        value = str(reply["result"]["thread"]["id"])
    except (KeyError, TypeError) as exc:
        raise RuntimeError("THREAD_START_ID_REQUIRED") from exc
    if not value:
        raise RuntimeError("THREAD_START_ID_REQUIRED")
    return value


def _workspace_hook_rows(
    reply: dict[str, Any],
    *,
    plugin_selector: str,
    workspace: Path,
) -> dict[str, dict[str, Any]]:
    """Return one clean trusted registry-derived inventory keyed by host event."""

    result = reply.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        raise TypeError("HOOKS_LIST_DATA_REQUIRED")
    expected_workspace = os.path.normcase(str(workspace.resolve()))
    matches = [
        row
        for row in data
        if isinstance(row, dict)
        and os.path.normcase(str(Path(str(row.get("cwd") or "")).resolve()))
        == expected_workspace
    ]
    if len(matches) != 1:
        raise RuntimeError("ONE_HOOK_WORKSPACE_REQUIRED")
    entry = matches[0]
    if entry.get("errors") or entry.get("warnings"):
        raise RuntimeError("INSTALLED_HOOK_DIAGNOSTICS_PRESENT")
    rows = [
        dict(row)
        for row in entry.get("hooks") or []
        if isinstance(row, dict) and row.get("pluginId") == plugin_selector
    ]
    by_host = {str(row.get("eventName") or ""): row for row in rows}
    if (
        set(by_host) != set(_CANONICAL_TO_HOST.values())
        or len(rows) != len(_CANONICAL_TO_HOST)
    ):
        raise RuntimeError("INSTALLED_HOOK_EVENT_INVENTORY_MISMATCH")
    for host_event, row in by_host.items():
        if (
            row.get("source") != "plugin"
            or row.get("isManaged") is not False
            or row.get("trustStatus") != "trusted"
            or row.get("handlerType") != "command"
            or not isinstance(row.get("enabled"), bool)
            or not str(row.get("key") or "").startswith(f"{plugin_selector}:")
            or not str(row.get("currentHash") or "").startswith("sha256:")
            or not str(row.get("sourcePath") or "")
        ):
            raise RuntimeError(f"INSTALLED_HOOK_AUTHORITY_MISMATCH:{host_event}")
    return by_host


def _config_user_version(
    client: _AppServerClient,
    *,
    codex_home: Path,
    request_id: int,
) -> str:
    reply = client.request(
        request_id,
        "config/read",
        {"cwd": None, "includeLayers": True},
    )
    result = reply.get("result")
    layers = result.get("layers") if isinstance(result, dict) else None
    config_path = codex_home.resolve() / "config.toml"
    expected = os.path.normcase(str(config_path))
    versions = []
    for layer in layers or []:
        if not isinstance(layer, dict):
            continue
        name = layer.get("name")
        if (
            isinstance(name, dict)
            and name.get("type") == "user"
            and os.path.normcase(str(Path(str(name.get("file") or "")).resolve()))
            == expected
        ):
            versions.append(str(layer.get("version") or ""))
    if len(versions) != 1 or not versions[0].startswith("sha256:"):
        raise RuntimeError("ONE_WRITABLE_USER_CONFIG_VERSION_REQUIRED")
    return versions[0]


def _set_progressive_hook_state(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    workspace: Path,
    plugin_selector: str,
    event_name: str,
    enabled: bool,
) -> dict[str, Any]:
    """CAS-write one trusted hook state and prove unrelated states unchanged."""

    host_event = _CANONICAL_TO_HOST[event_name]
    config_path = codex_home.resolve() / "config.toml"
    if not config_path.is_file():
        raise RuntimeError("CODEX_USER_CONFIG_REQUIRED")
    config_before = hashlib.sha256(config_path.read_bytes()).hexdigest().upper()
    with _LoopbackResponsesServer() as mock:
        client = _AppServerClient(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            base_url=mock.base_url,
        )
        try:
            client.request(
                24200,
                "initialize",
                {
                    "clientInfo": {
                        "name": "evidence_lane_progressive_hook_state",
                        "title": "Evidence Lane Progressive Hook State",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            client.send({"method": "initialized", "params": {}})
            before_reply = client.request(
                24201,
                "hooks/list",
                {"cwds": [str(workspace)]},
            )
            before = _workspace_hook_rows(
                before_reply,
                plugin_selector=plugin_selector,
                workspace=workspace,
            )
            target = before[host_event]
            states_before = {
                name: bool(row["enabled"]) for name, row in before.items()
            }
            mutation_required = states_before[host_event] is not enabled
            config_version = None
            if mutation_required:
                version = _config_user_version(
                    client,
                    codex_home=codex_home,
                    request_id=24202,
                )
                if hashlib.sha256(config_path.read_bytes()).hexdigest().upper() != config_before:
                    raise RuntimeError("CODEX_CONFIG_BASELINE_DRIFTED")
                write = client.request(
                    24203,
                    "config/batchWrite",
                    {
                        "edits": [
                            {
                                "keyPath": "hooks.state",
                                "value": {
                                    str(target["key"]): {
                                        "trusted_hash": str(target["currentHash"]),
                                        "enabled": enabled,
                                    }
                                },
                                "mergeStrategy": "upsert",
                            }
                        ],
                        "filePath": None,
                        "expectedVersion": version,
                        "reloadUserConfig": True,
                    },
                    allow_error=True,
                )
                if write.get("error"):
                    raise RuntimeError("CODEX_CONFIG_BATCH_WRITE_FAILED")
                write_result = write.get("result")
                if not isinstance(write_result, dict) or write_result.get("status") != "ok":
                    raise RuntimeError("CODEX_CONFIG_BATCH_WRITE_UNSEALED")
                config_version = str(write_result.get("version") or "")
            after_reply = client.request(
                24204,
                "hooks/list",
                {"cwds": [str(workspace)]},
            )
            after = _workspace_hook_rows(
                after_reply,
                plugin_selector=plugin_selector,
                workspace=workspace,
            )
            states_after = {name: bool(row["enabled"]) for name, row in after.items()}
            if states_after[host_event] is not enabled:
                raise RuntimeError("PROGRESSIVE_HOOK_STATE_NOT_PERSISTED")
            if any(
                name != host_event and states_after[name] != state
                for name, state in states_before.items()
            ):
                raise RuntimeError("UNRELATED_HOOK_STATE_MUTATED")
            if any(
                after[name].get("key") != row.get("key")
                or after[name].get("currentHash") != row.get("currentHash")
                or after[name].get("trustStatus") != "trusted"
                for name, row in before.items()
            ):
                raise RuntimeError("INSTALLED_HOOK_IDENTITY_DRIFTED")
            config_after = hashlib.sha256(config_path.read_bytes()).hexdigest().upper()
            body: dict[str, Any] = {
                "schema": "evidence-lane.codex-progressive-hook-state.v1",
                "status": "PASS",
                "plugin_selector": plugin_selector,
                "event_name": event_name,
                "host_event_name": host_event,
                "enabled_before": states_before[host_event],
                "enabled_after": states_after[host_event],
                "mutation_required": mutation_required,
                "supported_codex_api": ["hooks/list", "config/batchWrite"],
                "config_version_after": config_version,
                "config_sha256_before": config_before,
                "config_sha256_after": config_after,
                "unrelated_hook_state_mutated": False,
                "hook_key_sha256": hashlib.sha256(
                    str(target["key"]).encode("utf-8")
                ).hexdigest().upper(),
                "current_hash": str(target["currentHash"]),
                "raw_config_or_hook_path_included": False,
            }
            body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
            return body
        finally:
            client.close()


def _progressive_probe(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    workspace: Path,
    plugin_selector: str,
    event_name: str,
) -> dict[str, Any]:
    enable = _set_progressive_hook_state(
        executable=executable,
        codex_home=codex_home,
        data_root=data_root,
        workspace=workspace,
        plugin_selector=plugin_selector,
        event_name=event_name,
        enabled=True,
    )
    probe: dict[str, Any]
    fallback = None
    try:
        probe = _probe(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            workspace=workspace,
            plugin_selector=plugin_selector,
            required_events=(event_name,),
            isolated_codex_home=False,
        )
    except (InstalledHookReceiptError, OSError, RuntimeError, ValueError) as exc:
        probe = {
            "schema": PROBE_SCHEMA,
            "status": "FAIL_CLOSED",
            "code": str(exc),
            "raw_error_details_included": False,
        }
    if probe.get("status") != "PASS":
        fallback = _set_progressive_hook_state(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            workspace=workspace,
            plugin_selector=plugin_selector,
            event_name=event_name,
            enabled=False,
        )
    body: dict[str, Any] = {
        "schema": PROGRESSIVE_PROBE_SCHEMA,
        "status": "PASS" if probe.get("status") == "PASS" else "FAIL_CLOSED",
        "plugin_selector": plugin_selector,
        "event_name": event_name,
        "enable_receipt_sha256": enable["receipt_sha256"],
        "invocation_probe_receipt_sha256": probe.get("probe_receipt_sha256"),
        "invocation_status": probe.get("status"),
        "kept_enabled_after_pass": probe.get("status") == "PASS",
        "disabled_only_failing_hook": fallback is not None,
        "fallback_receipt_sha256": (
            fallback.get("receipt_sha256") if fallback is not None else None
        ),
        "unrelated_hook_state_mutated": False,
        "remote_model_or_service_called": False,
        "raw_hook_output_included": False,
        "probe": probe,
    }
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    return body


def _sanitized_hook_notification_summary(
    notifications: Iterable[dict[str, Any]],
    *,
    path_aliases: Iterable[Path] = (),
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for notification in notifications:
        method = str(notification.get("method") or "")
        if method not in {"hook/started", "hook/completed"}:
            continue
        params = notification.get("params")
        run = params.get("run") if isinstance(params, dict) else None
        if not isinstance(run, dict):
            continue
        entries = run.get("entries") or []
        visible_entries: list[dict[str, Any]] = []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = str(redact(str(entry.get("text") or "")))
                for path in path_aliases:
                    text = text.replace(str(path), "[LOCAL_PATH_REDACTED]")
                visible_entries.append(
                    {
                        "kind": str(entry.get("kind") or ""),
                        "text_after_redaction": text[:512],
                        "text_truncated": len(text) > 512,
                    }
                )
        records.append(
            {
                "method": method,
                "event_name": str(run.get("eventName") or ""),
                "status": str(run.get("status") or ""),
                "source": str(run.get("source") or ""),
                "handler_type": str(run.get("handlerType") or ""),
                "execution_mode": str(run.get("executionMode") or ""),
                "entry_count": len(entries) if isinstance(entries, list) else None,
                "entries_sha256": hashlib.sha256(_json_bytes(entries))
                .hexdigest()
                .upper(),
                "duration_ms": run.get("durationMs"),
                "entries_after_redaction": visible_entries,
                "raw_entries_included": False,
                "raw_paths_included": False,
            }
        )
    return records


def _sanitized_turn_summary(
    notifications: Iterable[dict[str, Any]],
    *,
    path_aliases: Iterable[Path] = (),
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for notification in notifications:
        if notification.get("method") != "turn/completed":
            continue
        params = notification.get("params")
        turn = params.get("turn") if isinstance(params, dict) else None
        if not isinstance(turn, dict):
            continue
        error = turn.get("error")
        message = str(error.get("message") or "") if isinstance(error, dict) else ""
        message = str(redact(message))
        for path in path_aliases:
            message = message.replace(str(path), "[LOCAL_PATH_REDACTED]")
        records.append(
            {
                "status": str(turn.get("status") or ""),
                "error_info": (
                    error.get("codexErrorInfo") if isinstance(error, dict) else None
                ),
                "message_after_redaction": message[:512],
                "message_truncated": len(message) > 512,
                "raw_turn_id_included": False,
            }
        )
    return records


def _probe(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    workspace: Path,
    plugin_selector: str,
    required_events: tuple[str, ...] | None = None,
    isolated_codex_home: bool = True,
) -> dict[str, Any]:
    with _LoopbackResponsesServer() as mock:
        client = _AppServerClient(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            base_url=mock.base_url,
        )
        try:
            client.request(
                17400,
                "initialize",
                {
                    "clientInfo": {
                        "name": "evidence_lane_row174_hook_verifier",
                        "title": "Evidence Lane Row174 Hook Verifier",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            client.send({"method": "initialized", "params": {}})
            hooks_reply = client.request(
                17401,
                "hooks/list",
                {"cwds": [str(workspace)]},
            )
            inventory = validate_installed_hook_inventory(
                hooks_reply,
                plugin_selector=plugin_selector,
                workspace=workspace,
                required_events=required_events,
            )
            thread_reply = client.request(
                17402,
                "thread/start",
                {
                    "cwd": str(workspace),
                    "model": "gpt-5.6-sol",
                    "modelProvider": "row174-loopback",
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "sessionStartSource": "startup",
                    "ephemeral": False,
                    "config": {
                        "model_provider": "row174-loopback",
                        "model_providers": {
                            "row174-loopback": {
                                "name": "Row174 Loopback",
                                "base_url": mock.base_url,
                                "env_key": "OPENAI_API_KEY",
                                "wire_api": "responses",
                                "request_max_retries": 0,
                                "stream_max_retries": 0,
                            }
                        },
                    },
                },
            )
            thread_id = _thread_id(thread_reply)
            client.request(
                17403,
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "text",
                            "text": (
                                "Run the read-only Get-Location command exactly once, "
                                "then reply that the isolated probe is complete."
                            ),
                        }
                    ],
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "readOnly",
                        "networkAccess": False,
                    },
                },
            )
            client.wait_for_notification("turn/completed")
            client.settle()
            compact_marker = len(client.notifications)
            client.request(
                17404,
                "thread/compact/start",
                {"threadId": thread_id},
            )
            client.wait_for_notification(
                "turn/completed",
                after_index=compact_marker,
            )
            client.settle()
            archive_marker = len(client.notifications)
            client.request(
                17405,
                "thread/archive",
                {"threadId": thread_id},
            )
            client.wait_for_notification(
                "thread/archived",
                after_index=archive_marker,
            )
            client.settle()
            try:
                invocation = build_invocation_receipt_from_codex_notifications(
                    inventory,
                    client.notifications,
                    host_session_id="row174-isolated-app-server",
                )
            except InstalledHookReceiptError as exc:
                body = {
                    "schema": PROBE_SCHEMA,
                    "status": "FAIL_CLOSED",
                    "code": str(exc),
                    "isolated_codex_home": isolated_codex_home,
                    "isolated_evidence_lane_data_root": True,
                    "loopback_only_responses_api": True,
                    "windows_process_window_mode": "CREATE_NO_WINDOW",
                    "live_plugin_selector_modified": False,
                    "remote_model_or_service_called": False,
                    "hook_notifications": _sanitized_hook_notification_summary(
                        client.notifications,
                        path_aliases=(codex_home, data_root, workspace, PLUGIN_ROOT),
                    ),
                    "turns": _sanitized_turn_summary(
                        client.notifications,
                        path_aliases=(codex_home, data_root, workspace, PLUGIN_ROOT),
                    ),
                    "mock_request_count": len(mock.requests),
                    "mock_requests": mock.requests,
                    "stderr_sha256": client.stderr_sha256,
                    "raw_stderr_included": False,
                    "raw_notifications_included": False,
                }
                body["probe_receipt_sha256"] = hashlib.sha256(
                    _json_bytes(body)
                ).hexdigest().upper()
                return body
            body: dict[str, Any] = {
                "schema": PROBE_SCHEMA,
                "status": invocation["status"],
                "isolated_codex_home": isolated_codex_home,
                "isolated_evidence_lane_data_root": True,
                "loopback_only_responses_api": True,
                "dummy_api_key_only": True,
                "read_only_tool_command": "Get-Location",
                "windows_process_window_mode": "CREATE_NO_WINDOW",
                "live_plugin_selector_modified": False,
                "remote_model_or_service_called": False,
                "inventory": inventory,
                "invocation": invocation,
                "mock_request_count": len(mock.requests),
                "mock_requests": mock.requests,
                "stderr_sha256": client.stderr_sha256,
                "raw_stderr_included": False,
                "raw_notifications_included": False,
                "raw_thread_or_turn_ids_included": False,
            }
            body["probe_receipt_sha256"] = hashlib.sha256(
                _json_bytes(body)
            ).hexdigest().upper()
            return body
        finally:
            client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-executable", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--plugin-selector", required=True)
    parser.add_argument(
        "--event",
        action="append",
        choices=(
            "SessionStart",
            "SubagentStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PermissionRequest",
            "PostToolUse",
            "PreCompact",
            "PostCompact",
            "SubagentStop",
            "Stop",
            "SessionEnd",
        ),
        help=(
            "Require and prove only this canonical event. Repeat for a bounded "
            "subset; omit to retain the complete registry release boundary."
        ),
    )
    parser.add_argument(
        "--live-codex-home",
        action="store_true",
        help="Record that the supplied Codex home is the active local host.",
    )
    parser.add_argument(
        "--progressive",
        action="store_true",
        help=(
            "Enable and prove exactly one --event on the live Codex home; keep it "
            "enabled on PASS or disable only it on failure."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        executable = args.codex_executable.resolve(strict=True)
        codex_home = args.codex_home.resolve(strict=True)
        data_root = args.data_root.resolve(strict=True)
        workspace = args.workspace.resolve(strict=True)
        if not executable.is_file():
            raise ValueError("CODEX_EXECUTABLE_FILE_REQUIRED")
        if not all(path.is_dir() for path in (codex_home, data_root, workspace)):
            raise ValueError("ISOLATED_DIRECTORY_REQUIRED")
        if args.progressive:
            if not args.live_codex_home or not args.event or len(args.event) != 1:
                raise ValueError("PROGRESSIVE_REQUIRES_ONE_LIVE_EVENT")
            result = _progressive_probe(
                executable=executable,
                codex_home=codex_home,
                data_root=data_root,
                workspace=workspace,
                plugin_selector=str(args.plugin_selector),
                event_name=str(args.event[0]),
            )
        else:
            result = _probe(
                executable=executable,
                codex_home=codex_home,
                data_root=data_root,
                workspace=workspace,
                plugin_selector=str(args.plugin_selector),
                required_events=(tuple(args.event) if args.event else None),
                isolated_codex_home=not bool(args.live_codex_home),
            )
    except (InstalledHookReceiptError, OSError, RuntimeError, ValueError) as exc:
        result = {
            "schema": PROBE_SCHEMA,
            "status": "FAIL_CLOSED",
            "code": str(exc),
            "live_plugin_selector_modified": False,
            "remote_model_or_service_called": False,
            "raw_error_details_included": False,
        }
        result["probe_receipt_sha256"] = hashlib.sha256(
            _json_bytes(result)
        ).hexdigest().upper()
    print(_json_bytes(result).decode("utf-8"))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
