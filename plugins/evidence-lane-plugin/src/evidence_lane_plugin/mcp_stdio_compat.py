"""Bounded stdio compatibility for modern discovery probes and MCP v1."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from io import TextIOWrapper
from typing import Any

import anyio
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError


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


@asynccontextmanager
async def discovery_fallback_stdio_server() -> AsyncIterator[tuple[Any, Any]]:
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
                    fallback = _discovery_fallback(line)
                    if fallback is not None:
                        await discovery_write.send(SessionMessage(fallback))
                        continue
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
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

    async with discovery_fallback_stdio_server() as (read_stream, write_stream):
        await server._mcp_server.run(
            read_stream,
            write_stream,
            server._mcp_server.create_initialization_options(),
        )
