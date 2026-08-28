from __future__ import annotations

import json
from pathlib import Path

import jsonschema

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = PLUGIN_ROOT.parents[1] / "apps" / "evidence-lane-app"
CONTRACT_PATH = PLUGIN_ROOT / "sdk" / "host" / "github-app-connection.v1.json"
SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "github-app-connection.schema.json"
ROUTE_ROOT = ADAPTER / "app" / "api" / "github-app"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_github_app_connection_contract_is_schema_valid_and_secret_free() -> None:
    contract = _json(CONTRACT_PATH)
    schema = _json(SCHEMA_PATH)
    jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    ).validate(contract)

    assert contract["secret_values_in_source"] is False
    assert contract["project_pv_secret_storage_allowed"] is False
    assert contract["workspace_secret_storage_allowed"] is False
    serialized = json.dumps(contract, sort_keys=True)
    for forbidden in ("gho_", "ghu_", "ghs_", "github_pat_", "BEGIN PRIVATE KEY"):
        assert forbidden not in serialized


def test_github_app_live_profiles_encode_github_mutual_exclusion() -> None:
    contract = _json(CONTRACT_PATH)
    primary = contract["primary_profile"]
    alternate = contract["alternate_profile"]
    assert primary == {
        "profile_id": "OAUTH_ON_INSTALL",
        "callback_urls": ["https://evidencelane.org/api/github-app/oauth/callback"],
        "request_oauth_on_install": True,
        "device_flow_enabled": True,
        "setup_url": None,
        "setup_on_update": False,
        "github_mutual_exclusion": primary["github_mutual_exclusion"],
    }
    assert alternate["request_oauth_on_install"] is False
    assert alternate["setup_url"] == "https://evidencelane.org/api/github-app/setup"
    assert alternate["setup_on_update"] is True


def test_github_app_backend_routes_are_complete_and_fail_closed() -> None:
    routes = {
        "oauth_start": ROUTE_ROOT / "oauth" / "start" / "route.ts",
        "oauth_callback": ROUTE_ROOT / "oauth" / "callback" / "route.ts",
        "setup": ROUTE_ROOT / "setup" / "route.ts",
        "device": ROUTE_ROOT / "device" / "route.ts",
        "webhook": ROUTE_ROOT / "webhook" / "route.ts",
        "testing": ROUTE_ROOT / "testing" / "route.ts",
    }
    for route in routes.values():
        assert route.is_file(), route

    start = routes["oauth_start"].read_text(encoding="utf-8")
    callback = routes["oauth_callback"].read_text(encoding="utf-8")
    device = routes["device"].read_text(encoding="utf-8")
    webhook = routes["webhook"].read_text(encoding="utf-8")
    testing = routes["testing"].read_text(encoding="utf-8")

    assert 'code_challenge_method", "S256"' in start
    assert "code_verifier: stored.codeVerifier" in callback
    assert "EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET" in callback
    assert "poll_token" in device
    assert "raw_device_code_returned: false" in device
    assert "device_code: device.device_code" not in device
    assert webhook.index("secureWebhookSignature") < webhook.index("JSON.parse")
    assert "timingSafeEqual" in (ROUTE_ROOT / "_lib.ts").read_text(encoding="utf-8")
    assert "installation_lifecycle_handled" in webhook
    assert "repository_selection_update_handled" in webhook
    assert "secretValuesReturned: false" in testing


def test_remote_adapter_exposes_executable_github_app_backend_test() -> None:
    package = _json(ADAPTER / "package.json")
    assert package["scripts"]["test:github-app-backend"] == (
        "node --experimental-strip-types tests/test-github-app-backend.mjs"
    )
