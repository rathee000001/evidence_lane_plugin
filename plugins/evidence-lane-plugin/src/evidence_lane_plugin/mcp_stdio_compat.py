"""Bounded stdio compatibility for modern discovery probes and MCP v1."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager, suppress
from io import TextIOWrapper
from typing import Any

import anyio
from mcp import types
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.tools.tool_manager import ToolManager
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

# Codex may add one collision-safe hexadecimal suffix to the installed native
# server's display namespace. That suffix is transport metadata only. Human
# release labels such as ``evidence_lane_v1_2_hil`` and cross-product names such
# as ``evidence_lane_chatgpt_read_only`` must never be normalized into native
# lifecycle authority.
_EVIDENCE_LANE_TOOL_NAMESPACE = re.compile(
    r"^evidence_lane(?:_[0-9a-f]{8,64})?$"
)


def _canonical_tool_name(
    requested_name: str,
    canonical_tool_names: Collection[str],
) -> str:
    """Return a registered bare name or preserve the request fail-closed."""

    if "." not in requested_name:
        return requested_name
    namespace, separator, canonical_name = requested_name.rpartition(".")
    if (
        separator
        and _EVIDENCE_LANE_TOOL_NAMESPACE.fullmatch(namespace) is not None
        and canonical_name in canonical_tool_names
    ):
        return canonical_name
    return requested_name


class EvidenceLaneToolManager(ToolManager):
    """Resolve host display namespaces without advertising alias tools."""

    def get_tool(self, name: str) -> Tool | None:
        canonical_name = _canonical_tool_name(name, self._tools)
        return super().get_tool(canonical_name)


def install_tool_namespace_compat(server: Any) -> Any:
    """Install transport-independent Evidence Lane lookup normalization."""

    current = server._tool_manager
    if isinstance(current, EvidenceLaneToolManager):
        return server
    server._tool_manager = EvidenceLaneToolManager(
        warn_on_duplicate_tools=current.warn_on_duplicate_tools,
        tools=current.list_tools(),
    )
    return server


def _discovery_fallback(raw_line: str) -> types.JSONRPCError | None:
    """Return the spec fallback response for a 2026-era discovery probe."""

    try:
        payload: Any = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("method") != "server/discover":
        return None
    request_id = payload.get("id")
    if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
        return None
    return types.JSONRPCError(
        jsonrpc="2.0",
        id=request_id,
        error=types.ErrorData(code=-32601, message="Method not found"),
    )


def _rewrite_tool_call_payload(
    payload: Any,
    canonical_tool_names: Collection[str],
) -> tuple[Any, bool]:
    """Normalize one host-generated Evidence Lane namespace fail-closed.

    Codex may give every connected MCP action a collision-safe host prefix,
    for example ``evidence_lane.render_project_panel`` or
    ``evidence_lane_7fb71d7f.render_project_panel``. Some tunnel profiles pass
    that display namespace through in ``tools/call`` even though MCP discovery
    correctly advertised the canonical bare name. Strip only a recognized
    Evidence Lane prefix and only when the prefix is either the exact native
    identity or its collision-safe hexadecimal display form and the suffix is
    an actually registered tool on this exact server. Legacy version labels,
    ChatGPT connector identities, unknown prefixes, and unknown tool suffixes
    remain unchanged so the canonical MCP dispatcher rejects them.
    """

    if isinstance(payload, list):
        changed = False
        rewritten_items: list[Any] = []
        for item in payload:
            rewritten, item_changed = _rewrite_tool_call_payload(
                item,
                canonical_tool_names,
            )
            rewritten_items.append(rewritten)
            changed = changed or item_changed
        return rewritten_items, changed

    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return payload, False
    params = payload.get("params")
    if not isinstance(params, dict):
        return payload, False
    requested_name = params.get("name")
    if not isinstance(requested_name, str) or "." not in requested_name:
        return payload, False

    canonical_name = _canonical_tool_name(requested_name, canonical_tool_names)
    if canonical_name == requested_name:
        return payload, False

    rewritten = dict(payload)
    rewritten_params = dict(params)
    rewritten_params["name"] = canonical_name
    rewritten["params"] = rewritten_params
    return rewritten, True


def _rewrite_namespaced_tool_calls(
    raw_line: str,
    canonical_tool_names: Collection[str],
) -> str:
    """Rewrite compatible ``tools/call`` names without changing other JSON-RPC."""

    try:
        payload: Any = json.loads(raw_line)
    except json.JSONDecodeError:
        return raw_line
    rewritten, changed = _rewrite_tool_call_payload(payload, canonical_tool_names)
    if not changed:
        return raw_line
    if raw_line.endswith("\r\n"):
        terminator = "\r\n"
    elif raw_line.endswith("\n"):
        terminator = "\n"
    else:
        terminator = ""
    return (
        json.dumps(rewritten, separators=(",", ":"), ensure_ascii=False)
        + terminator
    )


@asynccontextmanager
async def discovery_fallback_stdio_server(
    canonical_tool_names: Collection[str] = (),
) -> AsyncIterator[tuple[Any, Any]]:
    """Serve stdio while cleanly negotiating modern clients down to MCP v1.

    The July 2026 protocol tells a modern stdio client to fall back to the
    legacy ``initialize`` handshake when ``server/discover`` returns -32601.
    MCP Python SDK 1.28.1 predates that request and otherwise emits a verbose
    Pydantic union error. This transport intercepts only that one probe.
    """

    stdin_text = TextIOWrapper(
        sys.stdin.buffer,
        encoding="utf-8",
        errors="replace",
    )
    stdout_text = TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    stdin = anyio.wrap_file(stdin_text)
    stdout = anyio.wrap_file(stdout_text)
    read_send, read_receive = anyio.create_memory_object_stream(0)
    write_send, write_receive = anyio.create_memory_object_stream(0)
    discovery_write = write_send.clone()

    async def stdin_reader() -> None:
        try:
            async with read_send, discovery_write:
                async for line in stdin:
                    normalized_line = _rewrite_namespaced_tool_calls(
                        line,
                        canonical_tool_names,
                    )
                    fallback = _discovery_fallback(normalized_line)
                    if fallback is not None:
                        await discovery_write.send(SessionMessage(fallback))
                        continue
                    try:
                        message = types.JSONRPCMessage.model_validate_json(
                            normalized_line
                        )
                    except ValidationError as exc:
                        await read_send.send(exc)
                        continue
                    await read_send.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_receive:
                async for session_message in write_receive:
                    serialized = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    await stdout.write(serialized + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(stdin_reader)
            task_group.start_soon(stdout_writer)
            yield read_receive, write_send
    finally:
        with suppress(OSError, ValueError):
            await stdout.flush()
        # The UTF-8 wrappers own no process handle. Detach them so cleanup of
        # an in-process server cannot close pytest/Codex standard streams.
        with suppress(OSError, ValueError):
            stdin_text.detach()
        with suppress(OSError, ValueError):
            stdout_text.detach()


async def run_discovery_compatible_stdio(server: Any) -> None:
    """Run one pinned FastMCP server on the discovery-compatible transport."""

    tool_manager = getattr(server, "_tool_manager", None)
    registered_tools = getattr(tool_manager, "_tools", {})
    canonical_tool_names = frozenset(str(name) for name in registered_tools)
    async with discovery_fallback_stdio_server(canonical_tool_names) as (
        read_stream,
        write_stream,
    ):
        await server._mcp_server.run(
            read_stream,
            write_stream,
            server._mcp_server.create_initialization_options(),
        )
