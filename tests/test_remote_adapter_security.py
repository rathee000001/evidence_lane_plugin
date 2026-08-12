from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
PUBLIC_SITE = PLUGIN / "remote_adapter"


def test_v2_has_no_active_chatgpt_remote_mcp_adapter() -> None:
    assert not (PLUGIN / "chatgpt-app-connection.json").exists()
    assert not (PLUGIN / "chatgpt-app-submission.json").exists()
    assert not (PUBLIC_SITE / ".env.example").exists()
    assert not (PUBLIC_SITE / "api" / "index.py").exists()
    assert not (PUBLIC_SITE / "requirements.txt").exists()


def test_public_site_is_not_an_mcp_transport() -> None:
    vercel = json.loads((PUBLIC_SITE / "vercel.json").read_text(encoding="utf-8"))
    assert vercel == {"$schema": "https://openapi.vercel.sh/vercel.json"}
    connect = (PUBLIC_SITE / "app" / "connect" / "page.tsx").read_text(
        encoding="utf-8"
    )
    assert "local native MCP server" in connect
    assert "website or external transport" in connect
    assert "ChatGPT" in connect and "Deferred" in connect
    assert "/mcp" not in connect
    assert "openai-apps-challenge" not in connect


def test_public_cross_surface_assets_remain_present() -> None:
    required_pages = (
        "architecture",
        "connect",
        "credits",
        "lanes",
        "privacy",
        "proof",
        "provenance",
        "readme",
        "security",
        "support",
        "terms",
    )
    for page in required_pages:
        assert (PUBLIC_SITE / "app" / page / "page.tsx").is_file(), page
    for asset in (
        "evidence-lane-full-logo.png",
        "evidence-lane-icon.png",
        "evidence-static-brain.png",
    ):
        assert (PUBLIC_SITE / "public" / asset).is_file(), asset
