from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

from evidence_lane_plugin.constants import ENGINE_VERSION

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
ADAPTER = PLUGIN / "remote_adapter"
SCRIPTS = PLUGIN / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_release_candidate_rehearsal import _source_inventory

RELEASE = "3.0.0"
CODEX_RELEASE = "3.0.0+codex.20260816074428"
PUBLIC_SITE_SNAPSHOT_RELEASE = "3.0.0"
SITE = "https://evidencelane.org"
REPOSITORY = "https://github.com/rathee000001/evidence_lane_plugin"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_repository_markdown_path_link_resolves() -> None:
    excluded = {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".runtime",
        ".venv",
        ".vercel",
        "__pycache__",
        "node_modules",
        "tests",
    }
    derived_evidence_roots = {
        # Immutable receipts and extracted rehearsal copies are evidence, not
        # publishable repository documentation.  Their relative links belong
        # to the source package they record and must never be rewritten merely
        # to satisfy the live documentation surface.
        ROOT / "evidence",
    }
    markdown = sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in {".md", ".markdown"}
        and not (set(path.relative_to(ROOT).parts) & excluded)
        and not any(
            path.is_relative_to(derived_root)
            for derived_root in derived_evidence_roots
        )
    )
    assert len(markdown) >= 80
    assert {
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "docs" / "ARCHITECTURE.md",
    }.issubset(markdown)
    patterns = (
        re.compile(r"!?\[[^\]]*\]\(([^)]+)\)"),
        re.compile(
            r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE
        ),
    )
    failures: list[tuple[str, str, str]] = []
    for path in markdown:
        text = _read(path)
        for pattern in patterns:
            for raw_target in pattern.findall(text):
                target = raw_target.strip()
                if " " in target and not target.startswith("<"):
                    target = target.split(" ", 1)[0]
                target = target.strip("<>")
                lowered = target.casefold()
                if (
                    not target
                    or target.startswith(("#", "/"))
                    or lowered.startswith(
                        ("data:", "http://", "https://", "javascript:", "mailto:")
                    )
                ):
                    continue
                clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
                if not clean:
                    continue
                if re.match(r"^[A-Za-z]:[\\/]", clean):
                    failures.append(
                        (path.relative_to(ROOT).as_posix(), target, "absolute-local")
                    )
                    continue
                resolved = (path.parent / clean).resolve()
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    failures.append(
                        (path.relative_to(ROOT).as_posix(), target, "outside-repository")
                    )
                    continue
                if not resolved.exists():
                    failures.append(
                        (path.relative_to(ROOT).as_posix(), target, "missing")
                    )
    assert failures == []


def test_release_identity_urls_and_proprietary_boundary_are_consistent() -> None:
    root_project = tomllib.loads(_read(ROOT / "pyproject.toml"))["project"]
    plugin_project = tomllib.loads(_read(PLUGIN / "pyproject.toml"))["project"]
    adapter_package = json.loads(_read(ADAPTER / "package.json"))
    codex_manifest = json.loads(_read(PLUGIN / ".codex-plugin" / "plugin.json"))
    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    release_source = _read(ADAPTER / "app" / "_data" / "release-identity.ts")
    site_source = _read(ADAPTER / "app" / "_data" / "site.ts")

    assert root_project["version"] == RELEASE
    assert plugin_project["version"] == RELEASE
    assert adapter_package["version"] == RELEASE
    assert ENGINE_VERSION == RELEASE
    assert codex_manifest["version"] == CODEX_RELEASE
    assert public_metadata["version"] == PUBLIC_SITE_SNAPSHOT_RELEASE
    assert (
        f'export const releaseVersion = "{PUBLIC_SITE_SNAPSHOT_RELEASE}"'
        in release_source
    )
    assert public_metadata["plan_lane"]["production_role"] == (
        "PRE_HIL_BRANCH_PROJECTION_NOT_ACCEPTED_PUBLICATION"
    )
    assert PUBLIC_SITE_SNAPSHOT_RELEASE == RELEASE

    assert root_project["license"] == "LicenseRef-Proprietary"
    assert plugin_project["license"] == "LicenseRef-Proprietary"
    assert root_project["license-files"] == ["LICENSE.md"]
    assert plugin_project["license-files"] == ["LICENSE.md"]
    assert plugin_project["readme"] == "README.md"
    assert codex_manifest["license"] == "UNLICENSED"
    assert adapter_package["private"] is True
    assert (ROOT / "LICENSE.md").is_file()
    assert "proprietary" in _read(ROOT / "LICENSE.md").casefold()
    for relative in ("README.md", "LICENSE.md", "COPYRIGHT.md", "THIRD_PARTY_NOTICES.md"):
        assert (PLUGIN / relative).is_file()

    assert codex_manifest["repository"] == REPOSITORY
    assert codex_manifest["homepage"] == SITE
    assert codex_manifest["interface"]["websiteURL"] == SITE
    assert codex_manifest["interface"]["privacyPolicyURL"] == f"{SITE}/privacy"
    assert codex_manifest["interface"]["termsOfServiceURL"] == f"{SITE}/terms"
    assert public_metadata["repository"] == REPOSITORY
    assert public_metadata["homepage"] == SITE
    for exact in (
        f'export const publicSiteUrl = "{SITE}"',
        f'export const repositoryUrl = "{REPOSITORY}"',
    ):
        assert exact in site_source


def test_public_routes_sitemap_footer_and_plugin_presentation_are_complete() -> None:
    route_names = {
        "",
        "architecture",
        "ai-learning",
        "canon",
        "connect",
        "commands",
        "copyright",
        "credits",
        "hil",
        "hooks",
        "helper",
        "git-ci",
        "lanes",
        "license",
        "memory",
        "mcp",
        "operators",
        "plan",
        "privacy",
        "proof",
        "provenance",
        "release",
        "readme",
        "security",
        "skills",
        "studio",
        "support",
        "terms",
        "third-party",
        "tunnel",
    }
    for route in route_names:
        page = ADAPTER / "app" / route / "page.tsx" if route else ADAPTER / "app" / "page.tsx"
        assert page.is_file()

    sitemap = _read(ADAPTER / "app" / "sitemap.ts")
    for route in route_names - {""}:
        assert f'"/{route}"' in sitemap

    footer = _read(ADAPTER / "app" / "_components" / "site-footer.tsx")
    for route in (
        "privacy",
        "terms",
        "support",
        "readme",
        "license",
        "copyright",
        "third-party",
        "security",
        "credits",
        "commands",
        "helper",
        "tunnel",
    ):
        assert f'href="/{route}"' in footer

    connect = _read(ADAPTER / "app" / "connect" / "page.tsx")
    assert "endpointCardReady" in connect
    assert "endpointCardProtocol" in connect
    assert "local native MCP server" in connect
    assert "ChatGPT" in connect and "Deferred" in connect
    assert not (PLUGIN / "chatgpt-app-submission.json").exists()


def test_all_skill_manifests_are_unique_complete_and_package_owned() -> None:
    skill_files = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert len(skill_files) == 17
    names: list[str] = []
    descriptions: list[str] = []
    for path in skill_files:
        text = _read(path)
        frontmatter = text.split("---", 2)
        assert len(frontmatter) == 3
        name = re.search(r"^name:\s*(.+)$", frontmatter[1], re.MULTILINE)
        description = re.search(
            r"^description:\s*(.+)$", frontmatter[1], re.MULTILINE
        )
        assert name is not None
        assert description is not None
        assert name.group(1).strip() == path.parent.name
        assert len(description.group(1).strip()) >= 40
        names.append(name.group(1).strip())
        descriptions.append(description.group(1).strip())
    assert len(names) == len(set(names)) == 17
    assert len(descriptions) == len(set(descriptions)) == 17

    codex_mcp = json.loads(_read(PLUGIN / ".mcp.json"))
    assert codex_mcp["mcpServers"]["evidence-lane"]["command"] == "python"
    assert codex_mcp["mcpServers"]["evidence-lane"]["args"] == [
        "./scripts/run_mcp.py",
        "--transport",
        "stdio",
    ]
    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    assert "mcp_endpoint" not in public_metadata
    assert "website is documentation only" in public_metadata["interactive_ui"][
        "host_boundary"
    ]
    assert codex_mcp["mcpServers"]["evidence-lane"].get("url") is None


def test_release_package_excludes_local_state_maps_secrets_and_3d_dependencies() -> None:
    records, _ = _source_inventory(PLUGIN)
    names = [record["path"] for record in records]
    for required in ("README.md", "LICENSE.md", "COPYRIGHT.md", "THIRD_PARTY_NOTICES.md"):
        assert required in names
    forbidden_parts = {
        ".git",
        ".next",
        ".runtime",
        ".venv",
        ".vercel",
        "__pycache__",
        "node_modules",
    }
    assert all(not (set(Path(name).parts) & forbidden_parts) for name in names)
    assert all(
        not any(part.casefold().endswith(".egg-info") for part in Path(name).parts)
        for name in names
    )
    assert all(
        not (
            Path(name).name.casefold() == ".env"
            or (
                Path(name).name.casefold().startswith(".env.")
                and Path(name).name.casefold() != ".env.example"
            )
        )
        for name in names
    )
    assert all(
        not name.casefold().endswith((".map", ".tsbuildinfo", ".glb", ".gltf"))
        for name in names
    )
    assert all(not name.startswith("remote_adapter/") for name in names)

    adapter_package = json.loads(_read(ADAPTER / "package.json"))
    dependencies = {
        **adapter_package.get("dependencies", {}),
        **adapter_package.get("devDependencies", {}),
    }
    assert "three" in dependencies
    assert "@types/three" in dependencies
    assert not any("meshy" in name.casefold() for name in dependencies)
    webgl = _read(ADAPTER / "app" / "_components" / "ambient-evidence-field.tsx")
    assert 'import * as THREE from "three"' in webgl
    assert "WebGLRenderer" in webgl


def test_direct_dependency_license_correction_and_render_provenance_are_explicit() -> None:
    root_project = tomllib.loads(_read(ROOT / "pyproject.toml"))["project"]
    plugin_project = tomllib.loads(_read(PLUGIN / "pyproject.toml"))["project"]
    requirements = _read(ROOT / "requirements.in")
    lock = _read(PLUGIN / "requirements.lock.txt")
    active_python = "\n".join(
        _read(path)
        for base in (PLUGIN / "src", PLUGIN / "scripts")
        for path in sorted(base.rglob("*.py"))
    )

    for project in (root_project, plugin_project):
        dependencies = project["dependencies"]
        assert "pypdfium2==5.12.1" in dependencies
        assert not any("pymupdf" in dependency.casefold() for dependency in dependencies)
    assert "pypdfium2==5.12.1" in requirements
    assert "pypdfium2==5.12.1" in lock
    assert "PyMuPDF==" not in requirements
    assert "PyMuPDF==" not in lock
    assert "import fitz" not in active_python
    assert "fitz." not in active_python

    audit = ROOT / "docs" / "DEPENDENCY_LICENSE_AUDIT.md"
    assert audit.is_file()
    audit_text = _read(audit)
    assert "PyMuPDF==1.28.0" in audit_text
    assert "pypdfium2==5.12.1" in audit_text
    assert "SBOM" in audit_text
    assert "FIX-THEN-PURSUE (high confidence)" in audit_text
    assert "docs/DEPENDENCY_LICENSE_AUDIT.md" in _read(ROOT / "README.md")
    assert "DEPENDENCY_LICENSE_AUDIT.md" in _read(
        ROOT / "docs" / "CREDITS_AND_CONTRIBUTIONS.md"
    )
    assert "bundled PDFium and dependency license notices" in _read(
        ADAPTER / "app" / "credits" / "page.tsx"
    )

    index = json.loads(_read(ADAPTER / "app" / "_data" / "dummy-lane-artifacts.json"))
    assert len(index["lanes"]) == 18
    assert {
        lane["render"]["rasterizer"] for lane in index["lanes"]
    } == {"stable_svg_chromium_screenshot"}


def test_owner_repository_and_existing_devpost_identity_do_not_drift() -> None:
    site = _read(ADAPTER / "app" / "_data" / "site.ts")
    assert REPOSITORY in site
    assert "https://devpost.com/software/evidence_os" in site
    readme = _read(ROOT / "README.md")
    assert "1348634/evidence_os" in readme
    assert "only the existing Devpost project" in readme


def test_current_public_plan_projection_preserves_its_sealed_snapshot() -> None:
    execution = _read(ADAPTER / "app" / "_data" / "website-current-execution.ts")
    guidance = _read(ADAPTER / "app" / "_data" / "business-guidance.ts")
    snapshot = json.loads(
        _read(ADAPTER / "app" / "_data" / "website-plan-projection.json")
    )
    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    assert 'import planProjection from "./website-plan-projection.json"' in execution
    assert "websiteCurrentExecutionBoundary" in guidance
    assert snapshot["canonical_authority"] == "PLAN_LANE"
    assert snapshot["row_start"] == 81
    assert snapshot["task_count"] == len(snapshot["rows"])
    assert snapshot["row_end"] == snapshot["row_start"] + snapshot["task_count"] - 1
    assert snapshot["physically_final_hil_row"] == snapshot["row_end"]
    assert [row["row"] for row in snapshot["rows"]] == list(
        range(snapshot["row_start"], snapshot["row_end"] + 1)
    )
    assert [
        row["row"] for row in snapshot["rows"] if row["status"] == "IN_PROGRESS"
    ] == [snapshot["active_row"]]
    assert snapshot["rows"][-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert public_metadata["plan_lane"]["production_role"] == (
        "PRE_HIL_BRANCH_PROJECTION_NOT_ACCEPTED_PUBLICATION"
    )
    assert public_metadata["plan_lane"]["active_public_row"] == snapshot["active_row"]
    assert public_metadata["plan_lane"]["active_public_task_position"] == snapshot["active_task_position"]
    assert public_metadata["plan_lane"]["physically_final_hil_public_row"] == snapshot["physically_final_hil_row"]
    assert public_metadata["plan_lane"]["website_plan_snapshot_sha256"] == snapshot["snapshot_sha256"]
    current_plan = _read(ADAPTER / "app" / "_data" / "current-execution-plan.ts")
    assert "activeRow: websiteCurrentExecutionBoundary.activePublicOrder" in current_plan
    assert "activeTaskPosition: websiteCurrentExecutionBoundary.activeTaskPosition" in current_plan


def test_current_codex_surfaces_reject_active_chatgpt_delivery_claims() -> None:
    current_surfaces = {
        "README.md": _read(ROOT / "README.md"),
        "SECURITY.md": _read(ROOT / "SECURITY.md"),
        "docs/ARCHITECTURE.md": _read(ROOT / "docs" / "ARCHITECTURE.md"),
        "docs/VERSIONING.md": _read(ROOT / "docs" / "VERSIONING.md"),
        "docs/IMPLEMENTATION_TRACEABILITY.md": _read(
            ROOT / "docs" / "IMPLEMENTATION_TRACEABILITY.md"
        ),
        "plugins/evidence-lane-plugin/README.md": _read(PLUGIN / "README.md"),
        "plugins/evidence-lane-plugin/.codex-plugin/plugin.json": _read(
            PLUGIN / ".codex-plugin" / "plugin.json"
        ),
    }
    forbidden = (
        "ChatGPT-only Vercel adapter",
        "ChatGPT plugin is active",
        "ChatGPT installation is supported",
        "production ChatGPT remote MCP is active",
    )
    for relative, text in current_surfaces.items():
        for claim in forbidden:
            assert claim not in text, f"active external-host claim in {relative}: {claim}"

    public_metadata = json.loads(
        _read(ADAPTER / "public" / ".well-known" / "evidence-lane-plugin.json")
    )
    assert public_metadata["interactive_ui"]["host_boundary"].endswith(
        "This website is documentation only and does not transport lifecycle calls."
    )
    assert public_metadata["exposure_profiles"]["future_chatgpt"] == {
        "status": "INTENTIONALLY_DEFERRED_NOT_EXECUTABLE",
        "install_allowed": False,
    }
