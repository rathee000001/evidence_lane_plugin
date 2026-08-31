from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "evidence-lane-app" / "app"
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_github_pages import PAGES, build

ROUTE_DOCUMENTS = {
    "/": "README.md",
    "/readme": "README.md",
    "/memory": "docs/MEMORY.md",
    "/canon": "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md",
    "/ai-learning": "docs/AI_LEARNING.md",
    "/skills": "docs/SKILLS.md",
    "/mcp": "docs/MCP.md",
    "/hooks": "docs/HOOKS.md",
    "/plan": "docs/PLAN_AND_CHANGE_DISPLAY.md",
    "/git-ci": "docs/GIT_AND_CI_CD.md",
    "/architecture": "ARCHITECTURE.md",
    "/env-uop": "docs/ENV_AND_UOP.md",
    "/adaptive-delta": "docs/ADAPTIVE_DELTA_EXECUTION.md",
    "/lanes": "docs/SOURCE_INTAKE_AND_LANES.md",
    "/operators": "docs/ENV_AND_UOP.md",
    "/studio": "README.md",
    "/proof": "docs/REPOSITORY_MAP.md",
    "/provenance": "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    "/release": "docs/RELEASE_AND_COMPATIBILITY.md",
    "/connect": "docs/HOST_AND_STORAGE_MATRIX.md",
    "/hil": "docs/LIFECYCLE_AND_HIL.md",
    "/privacy": "SECURITY.md",
    "/security": "SECURITY.md",
    "/terms": "docs/TERMS_AND_CONDITIONS.md",
    "/license": "LICENSE.md",
    "/copyright": "docs/COPYRIGHT.md",
    "/third-party": "docs/THIRD_PARTY_LICENSES.md",
    "/credits": "docs/CREDITS_AND_CONTRIBUTIONS.md",
    "/support": "README.md",
    "/tunnel": "docs/USER_TUNNEL_GUIDE.md",
}


def _tracked_paths() -> set[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
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
    css = (ROOT / "github-pages" / "assets" / "site.css").read_text(encoding="utf-8")
    tabs_rule = re.search(r"\.tabs \{([^}]+)\}", css)

    assert tabs_rule is not None
    assert "flex-wrap: wrap" in tabs_rule.group(1)
    assert "overflow-x: visible" in tabs_rule.group(1)
    assert "overflow-x: auto" not in tabs_rule.group(1)


def test_github_pages_complete_projection_is_current_and_receipted(
    tmp_path: Path,
) -> None:
    receipt = build(tmp_path / "pages")
    refresh = receipt["documentation_refresh"]

    assert refresh["status"] == "PASS"
    assert refresh["current_release"] == "3.0.0"
    assert refresh["scope"] == "ALL_GITHUB_DOCUMENTS_AND_ALL_GITHUB_PAGES_EVERY_COMMIT"
    assert refresh["page_count"] == len(PAGES) == 29
    assert refresh["source_paths"] == sorted({source for _, _, source in PAGES})
    assert len(refresh["source_set_sha256"]) == 64

    generated = {
        (row["slug"], row["source"]): row["source_sha256"] for row in receipt["pages"]
    }
    assert set(generated) == {(slug, source) for slug, _, source in PAGES}
    assert all(len(digest) == 64 for digest in generated.values())
    for _, _, source in PAGES:
        first_line = (ROOT / source).read_text(encoding="utf-8").splitlines()[0]
        assert "evidence-lane-public-docs-full-refresh: 3.0.0" in first_line


def test_github_pages_workflow_requires_every_source_refresh_per_commit() -> None:
    workflow = (ROOT / ".github/workflows/evidence-lane-github-pages.yml").read_text(
        encoding="utf-8"
    )
    assert "fetch-depth: 0" in workflow
    assert "immutable cumulative main baseline" in workflow
    assert "prepare_github_pages.py --require-current-commit-refresh" in workflow


def test_github_pages_refresh_supports_bounded_cumulative_correction_chain() -> None:
    source = (ROOT / "scripts" / "prepare_github_pages.py").read_text(
        encoding="utf-8"
    )

    assert '"merge-base", "--is-ancestor"' in source
    assert '_commit_range_refresh_paths(baseline_commit, source_commit)' in source
    assert '_git_revision(f"{source_commit}^") != baseline_commit' not in source


def test_readme_leads_with_public_site_and_pages_projection_links() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    title_offset = readme.index("# Evidence Lane")
    public_site_offset = readme.index("https://evidencelane.org", title_offset)
    pages_offset = readme.index(
        "https://rathee000001.github.io/evidence_lane_plugin/", title_offset
    )
    navigation_offset = readme.index('href="ARCHITECTURE.md"', title_offset)

    assert title_offset < public_site_offset < pages_offset < navigation_offset
