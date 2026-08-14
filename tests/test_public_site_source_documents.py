from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "plugins" / "evidence-lane-plugin" / "remote_adapter" / "app"

ROUTE_DOCUMENTS = {
    "/": "README.md",
    "/readme": "README.md",
    "/skills": "docs/SKILLS.md",
    "/mcp": "docs/MCP.md",
    "/hooks": "docs/HOOKS.md",
    "/architecture": "ARCHITECTURE.md",
    "/lanes": "docs/ARCHITECTURE.md",
    "/operators": "docs/HOST_STORAGE_ENV_MODE_CONTINUITY.md",
    "/studio": "README.md",
    "/proof": "docs/IMPLEMENTATION_TRACEABILITY.md",
    "/provenance": "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    "/connect": "docs/HOST_CAPABILITY_MATRIX.md",
    "/hil": "docs/FIRST_HIL_RUNBOOK.md",
    "/privacy": "SECURITY.md",
    "/security": "SECURITY.md",
    "/terms": "LICENSE.md",
    "/license": "LICENSE.md",
    "/copyright": "COPYRIGHT.md",
    "/credits": "docs/CREDITS_AND_CONTRIBUTIONS.md",
    "/support": "README.md",
}


def _tracked_paths() -> set[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return {value for value in output.split("\0") if value}


def test_every_public_route_has_one_git_markdown_authority() -> None:
    source = (APP / "_data" / "repository-documents.ts").read_text(encoding="utf-8")
    tracked = _tracked_paths()

    assert source.count('path: "') == len(ROUTE_DOCUMENTS)
    for route, document in ROUTE_DOCUMENTS.items():
        assert f'"{route}":' in source
        assert f'path: "{document}"' in source
        assert (ROOT / document).is_file(), document
        assert document in tracked, document


def test_shared_layout_exposes_the_route_authority_strip() -> None:
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")
    component = (APP / "_components" / "repository-source-strip.tsx").read_text(
        encoding="utf-8"
    )

    assert "<RepositorySourceStrip />" in layout
    assert "data-authority-document" in component
    assert "Open Markdown" in component


def test_primary_plugin_pages_are_first_class_routes() -> None:
    site = (APP / "_data" / "site.ts").read_text(encoding="utf-8")
    sitemap = (APP / "sitemap.ts").read_text(encoding="utf-8")

    for route in ("skills", "mcp", "hooks"):
        assert (APP / route / "page.tsx").is_file()
        assert f'{{ href: "/{route}",' in site
        assert f'"/{route}"' in sitemap
