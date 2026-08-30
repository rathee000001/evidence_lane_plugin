from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
DOC_BINDING = (
    ROOT / "apps" / "evidence-lane-app"
    / "app"
    / "_data"
    / "public-docs-backend-binding.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_historical_current_route_receipt_and_pointer_copies_are_purged() -> None:
    for relative in (
        "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260824.json",
        "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260824.md",
        ".agents/plugins/current-route-refresh.v1.json",
        ".github/current-route-refresh.v1.json",
        "docs/current-route-refresh.v1.json",
        "github-pages/current-route-refresh.v1.json",
        "plugins/current-route-refresh.v1.json",
        "scripts/current-route-refresh.v1.json",
        "tests/current-route-refresh.v1.json",
    ):
        assert not (ROOT / relative).exists(), relative


def test_current_public_docs_binding_hashes_every_maintained_document() -> None:
    binding = json.loads(DOC_BINDING.read_text(encoding="utf-8"))
    assert binding["schema"] == "evidence-lane.public-docs-backend-binding.v1"
    assert binding["status"] == "PASS"
    assert binding["historical_internal_source_count"] == 0
    rows = {row["path"]: row for row in binding["documents"]}
    assert len(rows) == binding["document_count"] == len(binding["documents"])
    assert binding["document_count"] > 0
    for relative, row in rows.items():
        path = ROOT / relative
        assert path.is_file(), relative
        assert path.stat().st_size == row["bytes"]
        assert _sha256(path) == row["sha256"]

    public = json.loads(
        (PLUGIN / "schemas" / "public-action-schemas.v001.json").read_text(
            encoding="utf-8"
        )
    )
    assert public["tool_count"] == 91
    assert public["read_tool_count"] == 30
    assert public["write_tool_count"] == 61
