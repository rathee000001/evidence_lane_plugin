from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = PLUGIN_ROOT.parents[1] / "apps" / "evidence-lane-app"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_remote_adapter_exposes_only_public_safe_transport_routes() -> None:
    actual = {
        path.relative_to(ADAPTER).as_posix()
        for path in (ADAPTER / "app" / "api").rglob("route.ts")
    }
    assert actual == {
        "app/api/backend-readiness/route.ts",
        "app/api/github-app/device/route.ts",
        "app/api/github-app/oauth/callback/route.ts",
        "app/api/github-app/oauth/start/route.ts",
        "app/api/github-app/setup/route.ts",
        "app/api/github-app/testing/route.ts",
        "app/api/github-app/webhook/route.ts",
        "app/api/studio-query/route.ts",
    }
    assert not (ADAPTER / "api").exists()
    assert not any(
        marker in path.casefold()
        for path in actual
        for marker in ("env", "uop", "lane", "sector", "toolchain")
    )


def test_runtime_behavior_stays_with_sdk_mcp_and_conditional_tunnel() -> None:
    contract = _json(PLUGIN_ROOT / "sdk" / "host" / "public-backend-readiness.v1.json")
    boundary = contract["runtime_routing_boundary"]
    assert boundary == {
        "remote_adapter_role": "PUBLIC_SAFE_TRANSPORT_ONLY",
        "internal_sdk_role": "ALL_PLUGIN_BEHAVIOR_OWNER",
        "native_mcp_role": "PUBLIC_ACTION_TRANSPORT",
        "tunnel_role": "CONDITIONAL_HOST_TOOL_GAP_TRANSPORT",
        "env_uop_role": "EXECUTABLE_AI_ACTION_PLANE_SEPARATE_AUTHORITIES",
        "toolchain_route": "ai_toolchain_route",
        "project_sector_http_api_exposed": False,
        "toolchain_http_api_exposed": False,
        "allowed_http_route_prefixes": [
            "/api/backend-readiness",
            "/api/github-app",
            "/api/studio-query",
        ],
    }

    internal_sdk = (
        PLUGIN_ROOT / "src" / "evidence_lane_plugin" / "internal_sdk.py"
    ).read_text(encoding="utf-8")
    mcp = (PLUGIN_ROOT / "src" / "evidence_lane_plugin" / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    assert "ALL_PLUGIN_BEHAVIOR_INTERNAL_SDK_GOVERNED" in internal_sdk
    assert '"ai_toolchain_route:READ"' in internal_sdk
    assert '("first_class_workflows", "ai_toolchain_route")' in internal_sdk
    assert '"ai_toolchain_route"' in mcp

    requirements = _json(
        PLUGIN_ROOT / "toolchains" / "tool-requirement-matrix.v1.json"
    )["requirements"]
    tunnel = _json(PLUGIN_ROOT / "toolchains" / "tunnel-runtime-toolchain.v1.json")
    assert len(requirements) == tunnel["requirement_count"] == 95
    assert [row["tool"] for row in requirements] == [
        row["tool"] for row in tunnel["requirements"]
    ]
