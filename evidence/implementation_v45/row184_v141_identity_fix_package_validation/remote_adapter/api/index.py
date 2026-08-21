"""ChatGPT-only Vercel ASGI adapter to one durable Evidence Lane MCP origin."""

from __future__ import annotations

import json
import os
import re
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx

_SHA = re.compile(r"[0-9a-fA-F]{40}")
_PROXY_TIMEOUT = httpx.Timeout(connect=15, read=285, write=30, pool=15)
_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
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


class RequestBodyTooLarge(ValueError):
    """Raised before an oversized request can be forwarded to the origin."""


def _public_origin_host(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        return "." in host


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
    elif not _public_origin_host(parsed.hostname or ""):
        errors.append("DURABLE_PUBLIC_ORIGIN_HOST_REQUIRED")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        errors.append("DURABLE_ORIGIN_ROOT_REQUIRED")
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
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] != "http.request":
            continue
        chunk = bytes(message.get("body", b""))
        size += len(chunk)
        if size > _MAX_REQUEST_BODY_BYTES:
            raise RequestBodyTooLarge
        chunks.append(chunk)
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
    return True, {
        "status": str(payload.get("status") or "PASS")[:64],
        "service": str(payload.get("service") or "durable-evidence-lane-origin")[:128],
        "release_sha": actual_sha,
    }


def _content_length(scope: dict[str, Any]) -> int | None:
    values = [
        value.decode("ascii", errors="strict").strip()
        for key, value in scope.get("headers", [])
        if key.lower() == b"content-length"
    ]
    if not values:
        return None
    if len(set(values)) != 1 or not values[0].isdigit():
        raise ValueError("AMBIGUOUS_OR_INVALID_CONTENT_LENGTH")
    return int(values[0])


def _external_route(scope: dict[str, Any]) -> tuple[str, str]:
    """Recover the public path carried through the Vercel catch-all rewrite."""

    path = str(scope.get("path") or "/")
    query = bytes(scope.get("query_string") or b"").decode("ascii", errors="ignore")
    pairs = parse_qsl(query, keep_blank_values=True)
    routed = [value for key, value in pairs if key == "__evi_path"]
    public_pairs = [(key, value) for key, value in pairs if key != "__evi_path"]
    if path == "/api/index.py" and routed:
        clean = routed[-1].strip().lstrip("/")
        path = "/" + clean if clean else "/"
    return path, urlencode(public_pairs, doseq=True)


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
    path, query = _external_route(scope)
    if path == "/":
        await _send_json(
            send,
            200,
            {
                "status": "READY" if configuration["valid"] else "READY_FAIL_CLOSED",
                "service": "evidence-lane-chatgpt-adapter",
                "release_sha": configuration["expected_sha"] or None,
                "vercel_git_commit_sha": configuration["deployment_sha"],
                "durable_origin_configured": bool(configuration["origin"]),
                "configuration_errors": configuration["errors"],
                "mcp_path": "/mcp",
                "health_path": "/healthz",
                "local_state_authority": False,
                "general_router": False,
            },
        )
        return
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
    try:
        declared_length = _content_length(scope)
    except (UnicodeDecodeError, ValueError):
        await _send_json(
            send,
            400,
            {"status": "BLOCKED", "code": "REQUEST_CONTENT_LENGTH_INVALID"},
        )
        return
    if declared_length is not None and declared_length > _MAX_REQUEST_BODY_BYTES:
        await _send_json(
            send,
            413,
            {"status": "BLOCKED", "code": "REQUEST_BODY_TOO_LARGE"},
        )
        return
    try:
        request_body = await _body(receive)
    except RequestBodyTooLarge:
        await _send_json(
            send,
            413,
            {"status": "BLOCKED", "code": "REQUEST_BODY_TOO_LARGE"},
        )
        return
    incoming_headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key.lower() not in _HOP_BY_HOP and key.lower() != b"host"
    }
    incoming_headers["x-evidence-lane-release-sha"] = configuration["expected_sha"]
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
