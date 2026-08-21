from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = (
    ROOT / "plugins" / "evidence-lane-plugin" / "remote_adapter" / "api" / "index.py"
)


def _adapter():
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_remote_adapter_security", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("origin", "code"),
    [
        ("https://localhost", "DURABLE_PUBLIC_ORIGIN_HOST_REQUIRED"),
        ("https://127.0.0.1", "DURABLE_PUBLIC_ORIGIN_HOST_REQUIRED"),
        ("https://169.254.169.254", "DURABLE_PUBLIC_ORIGIN_HOST_REQUIRED"),
        ("https://origin.example.com/base", "DURABLE_ORIGIN_ROOT_REQUIRED"),
        ("https://origin.example.com?target=other", "DURABLE_ORIGIN_ROOT_REQUIRED"),
    ],
)
def test_adapter_rejects_private_or_non_root_durable_origins(
    monkeypatch: pytest.MonkeyPatch, origin: str, code: str
) -> None:
    adapter = _adapter()
    monkeypatch.setenv("EVIDENCE_LANE_DURABLE_MCP_ORIGIN", origin)
    monkeypatch.setenv("EVIDENCE_LANE_RELEASE_SHA", "a" * 40)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "a" * 40)
    configuration = adapter._configuration()
    assert configuration["valid"] is False
    assert code in configuration["errors"]


def test_adapter_accepts_exact_public_root_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    monkeypatch.setenv(
        "EVIDENCE_LANE_DURABLE_MCP_ORIGIN", "https://tunnel.example.com/"
    )
    monkeypatch.setenv("EVIDENCE_LANE_RELEASE_SHA", "b" * 40)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "b" * 40)
    configuration = adapter._configuration()
    assert configuration["valid"] is True
    assert configuration["errors"] == []


def test_adapter_stops_streamed_body_after_two_megabytes() -> None:
    adapter = _adapter()
    chunks = iter(
        [
            {
                "type": "http.request",
                "body": b"a" * adapter._MAX_REQUEST_BODY_BYTES,
                "more_body": True,
            },
            {"type": "http.request", "body": b"b", "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    with pytest.raises(adapter.RequestBodyTooLarge):
        asyncio.run(adapter._body(receive))


def test_adapter_rejects_ambiguous_content_length() -> None:
    adapter = _adapter()
    with pytest.raises(ValueError, match="AMBIGUOUS_OR_INVALID_CONTENT_LENGTH"):
        adapter._content_length(
            {
                "headers": [
                    (b"content-length", b"12"),
                    (b"content-length", b"13"),
                ]
            }
        )


def test_adapter_normalizes_both_public_metadata_routes_to_exact_mcp_resource() -> None:
    adapter = _adapter()
    exact = "/.well-known/oauth-protected-resource/mcp"
    assert adapter._origin_path(exact) == exact
    assert adapter._origin_path("/.well-known/oauth-protected-resource") == exact
    assert adapter._origin_path("/mcp") == "/mcp"


def test_openai_apps_challenge_is_exact_plaintext_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    monkeypatch.delenv("EVIDENCE_LANE_OPENAI_APPS_CHALLENGE_TOKEN", raising=False)
    assert adapter._openai_apps_challenge_token() is None
    monkeypatch.setenv("EVIDENCE_LANE_OPENAI_APPS_CHALLENGE_TOKEN", "bad token")
    assert adapter._openai_apps_challenge_token() is None

    exact_token = "portal-verification-token_123456"
    monkeypatch.setenv("EVIDENCE_LANE_OPENAI_APPS_CHALLENGE_TOKEN", exact_token)
    assert adapter._openai_apps_challenge_token() == exact_token

    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        adapter.app(
            {
                "type": "http",
                "path": "/.well-known/openai-apps-challenge",
                "method": "GET",
                "headers": [],
                "query_string": b"",
            },
            receive,
            send,
        )
    )
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == exact_token.encode("ascii")
