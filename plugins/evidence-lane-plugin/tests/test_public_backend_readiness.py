from __future__ import annotations

import json
from pathlib import Path

import jsonschema

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = PLUGIN_ROOT.parents[1] / "apps" / "evidence-lane-remote-adapter"
CONTRACT = PLUGIN_ROOT / "sdk" / "host" / "public-backend-readiness.v1.json"
SCHEMA = PLUGIN_ROOT / "schemas" / "public-backend-readiness.schema.json"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_backend_readiness_contract_is_valid_and_secret_free() -> None:
    contract = _json(CONTRACT)
    jsonschema.Draft202012Validator(
        _json(SCHEMA),
        format_checker=jsonschema.FormatChecker(),
    ).validate(contract)
    assert contract["secret_values_exposed"] is False
    assert contract["project_authority_mutation"] is False
    assert contract["candidate_or_hil_authority"] is False
    encoded = json.dumps(contract, sort_keys=True)
    for forbidden in ("gho_", "github_pat_", "sk-or-v1-", "BEGIN PRIVATE KEY"):
        assert forbidden not in encoded


def test_backend_links_exist_without_page_or_rag_ownership() -> None:
    contract = _json(CONTRACT)
    surfaces = contract["surfaces"]
    assert surfaces["connect"]["testing_path"] == "/api/github-app/testing"
    assert surfaces["prompt_studio"]["query_path"] == "/api/studio-query"
    assert surfaces["proof"]["public_path"] == "/proof"
    assert surfaces["git_ci"]["public_path"] == "/git-ci"
    assert all(contract["presentation_deferrals"].values())
    assert (ADAPTER / "app" / "api" / "backend-readiness" / "route.ts").is_file()
    projected = _json(
        ADAPTER / "app" / "_data" / "public-backend-readiness.v1.json"
    )
    assert projected == contract
