"""ChatGPT-only Vercel ASGI adapter to one durable Evidence Lane MCP origin."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

_SHA = re.compile(r"[0-9a-fA-F]{40}")
_PROXY_TIMEOUT = httpx.Timeout(connect=15, read=285, write=30, pool=15)
_HOP_BY_HOP = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailers",
    b"transfer-encoding",
    b"upgrade",
}


def _configuration() -> dict[str, Any]:
    origin = os.environ.get("EVIDENCE_LANE_DURABLE_MCP_ORIGIN", "").strip().rstrip("/")
    expected_sha = os.environ.get("EVIDENCE_LANE_RELEASE_SHA", "").strip().lower()
    deployment_sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "").strip().lower()
    parsed = urlparse(origin)
    errors: list[str] = []
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        errors.append("DURABLE_HTTPS_ORIGIN_REQUIRED")
    if not _SHA.fullmatch(expected_sha):
        errors.append("EXACT_RELEASE_SHA_REQUIRED")
    if deployment_sha and deployment_sha != expected_sha:
        errors.append("VERCEL_GIT_SHA_MISMATCH")
    return {
        "origin": origin,
        "expected_sha": expected_sha,
        "deployment_sha": deployment_sha or None,
        "valid": not errors,
        "errors": errors,
    }


async def _send_json(send: Any, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _origin_health(configuration: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(configuration["origin"] + "/healthz")
        payload = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
    except (httpx.HTTPError, ValueError):
        return False, {"code": "DURABLE_ORIGIN_UNREACHABLE"}
    actual_sha = str(payload.get("release_sha") or "").lower()
    if response.status_code != 200 or actual_sha != configuration["expected_sha"]:
        return False, {
            "code": "DURABLE_ORIGIN_RELEASE_MISMATCH",
            "origin_status": response.status_code,
            "expected_release_sha": configuration["expected_sha"],
            "actual_release_sha": actual_sha or None,
        }
    return True, payload


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    configuration = _configuration()
    path = str(scope.get("path") or "/")
    if path == "/healthz":
        if not configuration["valid"]:
            await _send_json(
                send,
                503,
                {
                    "status": "BLOCKED",
                    "service": "evidence-lane-chatgpt-adapter",
                    "errors": configuration["errors"],
                    "local_state_authority": False,
                },
            )
            return
        healthy, origin = await _origin_health(configuration)
        await _send_json(
            send,
            200 if healthy else 503,
            {
                "status": "PASS" if healthy else "BLOCKED",
                "service": "evidence-lane-chatgpt-adapter",
                "release_sha": configuration["expected_sha"],
                "vercel_git_commit_sha": configuration["deployment_sha"],
                "durable_origin_verified": healthy,
                "origin": origin,
                "local_state_authority": False,
                "general_router": False,
            },
        )
        return
    if path not in {"/mcp", "/.well-known/oauth-protected-resource"}:
        await _send_json(send, 404, {"status": "NOT_FOUND"})
        return
    if not configuration["valid"]:
        await _send_json(
            send,
            503,
            {"status": "BLOCKED", "errors": configuration["errors"]},
        )
        return
    healthy, _ = await _origin_health(configuration)
    if not healthy:
        await _send_json(
            send,
            503,
            {"status": "BLOCKED", "code": "DURABLE_ORIGIN_RELEASE_NOT_VERIFIED"},
        )
        return
    request_body = await _body(receive)
    incoming_headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key.lower() not in _HOP_BY_HOP and key.lower() != b"host"
    }
    incoming_headers["x-evidence-lane-release-sha"] = configuration["expected_sha"]
    query = bytes(scope.get("query_string") or b"").decode("ascii", errors="ignore")
    target = configuration["origin"] + path + (("?" + query) if query else "")
    response_started = False
    try:
        async with (
            httpx.AsyncClient(timeout=_PROXY_TIMEOUT, follow_redirects=False) as client,
            client.stream(
                str(scope.get("method") or "GET"),
                target,
                headers=incoming_headers,
                content=request_body,
            ) as response,
        ):
            headers = [
                (key.encode("latin-1"), value.encode("latin-1"))
                for key, value in response.headers.items()
                if key.lower().encode("ascii", errors="ignore") not in _HOP_BY_HOP
            ]
            headers.extend(
                [
                    (b"cache-control", b"no-store"),
                    (
                        b"x-evidence-lane-release-sha",
                        configuration["expected_sha"].encode("ascii"),
                    ),
                ]
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": headers,
                }
            )
            response_started = True
            async for chunk in response.aiter_bytes():
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
    except httpx.HTTPError:
        if response_started:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await _send_json(
                send,
                502,
                {"status": "BLOCKED", "code": "DURABLE_ORIGIN_PROXY_FAILED"},
            )
