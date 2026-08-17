from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "plugins" / "evidence-lane-plugin" / "remote_adapter" / "app"

ROUTE_DOCUMENTS = {
    "/": "README.md",
    "/readme": "README.md",
    "/memory": "docs/MEMORY.md",
    "/canon": "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md",
    "/ai-learning": "docs/AI_LEARNING.md",
    "/skills": "docs/SKILLS.md",
    "/mcp": "docs/MCP.md",
    "/hooks": "docs/HOOKS.md",
    "/commands": "docs/COMMANDS.md",
    "/plan": "docs/PLAN_AND_CHANGE_DISPLAY.md",
    "/git-ci": "docs/GIT_AND_CI_CD.md",
    "/architecture": "ARCHITECTURE.md",
    "/lanes": "docs/ARCHITECTURE.md",
    "/operators": "docs/HOST_STORAGE_ENV_MODE_CONTINUITY.md",
    "/studio": "README.md",
    "/proof": "docs/IMPLEMENTATION_TRACEABILITY.md",
    "/provenance": "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    "/release": "docs/RELEASE_AND_COMPATIBILITY.md",
    "/connect": "docs/HOST_CAPABILITY_MATRIX.md",
    "/hil": "docs/FIRST_HIL_RUNBOOK.md",
    "/privacy": "SECURITY.md",
    "/security": "SECURITY.md",
    "/terms": "docs/TERMS_AND_CONDITIONS.md",
    "/license": "LICENSE.md",
    "/copyright": "docs/COPYRIGHT.md",
    "/third-party": "plugins/evidence-lane-plugin/THIRD_PARTY_NOTICES.md",
    "/credits": "docs/CREDITS_AND_CONTRIBUTIONS.md",
    "/support": "README.md",
    "/helper": "docs/USER_HELPER_GUIDE.md",
    "/tunnel": "docs/USER_TUNNEL_GUIDE.md",
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


def test_shared_layout_does_not_render_repository_authority_strip() -> None:
    layout = (APP / "layout.tsx").read_text(encoding="utf-8")

    assert "RepositorySourceStrip" not in layout
    assert not (APP / "_components" / "repository-source-strip.tsx").exists()


def test_primary_plugin_pages_are_first_class_routes() -> None:
    site = (APP / "_data" / "site.ts").read_text(encoding="utf-8")
    sitemap = (APP / "sitemap.ts").read_text(encoding="utf-8")

    for route in (
        "memory",
        "canon",
        "ai-learning",
        "skills",
        "mcp",
        "hooks",
        "commands",
        "git-ci",
    ):
        assert (APP / route / "page.tsx").is_file()
        assert f'{{ href: "/{route}",' in site
        assert f'"/{route}"' in sitemap

    for deep_linked_route in ("plan", "release"):
        assert (APP / deep_linked_route / "page.tsx").is_file()
        assert f'{{ href: "/{deep_linked_route}",' not in site
        assert f'"/{deep_linked_route}"' in sitemap


def test_github_pages_navigation_wraps_without_horizontal_scroll() -> None:
    css = (ROOT / "github-pages" / "assets" / "site.css").read_text(
        encoding="utf-8"
    )
    tabs_rule = re.search(r"\.tabs \{([^}]+)\}", css)

    assert tabs_rule is not None
    assert "flex-wrap: wrap" in tabs_rule.group(1)
    assert "overflow-x: visible" in tabs_rule.group(1)
    assert "overflow-x: auto" not in tabs_rule.group(1)


def test_readme_leads_with_public_site_and_pages_projection_links() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    title_offset = readme.index("# Evidence Lane 3.0.0")
    public_site_offset = readme.index("https://evidencelane.org", title_offset)
    pages_offset = readme.index(
        "https://rathee000001.github.io/evidence_lane_plugin/", title_offset
    )
    navigation_offset = readme.index('href="ARCHITECTURE.md"', title_offset)

    assert title_offset < public_site_offset < pages_offset < navigation_offset
