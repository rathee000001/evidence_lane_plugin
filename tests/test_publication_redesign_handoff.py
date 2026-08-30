from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_publication_redesign_handoff.py"
OUTPUT = (
    ROOT
    / "apps"
    / "evidence-lane-app"
    / "app"
    / "_data"
    / "publication-redesign-handoff.v1.json"
)


def _module():
    spec = importlib.util.spec_from_file_location("publication_redesign_handoff", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publication_handoff_is_route_complete_and_deferred() -> None:
    module = _module()
    expected = module.build_contract()
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == expected
    page_routes = {row["route"] for row in committed["pages"]}
    api_routes = {row["route"] for row in committed["api_routes"]}
    assert len(page_routes) == committed["page_count"] == 28
    assert len(api_routes) == committed["api_route_count"] == 8
    assert all(
        row["status"] == "AWAITING_VISUAL_CONCEPT_AND_USER_CORRECTION"
        and row["implementation_authorized"] is False
        and row["publication_authorized"] is False
        and row["existing_ui_claim_authority"] is False
        for row in committed["pages"]
    )
    assert all(
        row["status"] == "BACKEND_PARITY_ONLY_NO_PUBLIC_REDESIGN"
        and row["behavior_change_authorized"] is False
        for row in committed["api_routes"]
    )
    assert committed["envato_or_adobe_asset_selected"] is False
    assert committed["external_concept_images_are_repository_assets"] is False
    assert committed["github_pages_and_vercel_publication_authorized"] is False
    assert len(committed["asset_license_ledger_required_fields"]) == 13
