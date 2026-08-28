from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = "3.0.0"
V13_PATTERN = re.compile(r"(?i)(?:\bv1\.3(?:\.0)?\b|\b1\.3\.0\b)")

HISTORICAL_OR_DEPENDENCY_FILES = {
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/CHATGPT_CONNECTION.md",
    "docs/IMPLEMENTATION_TRACEABILITY.md",
    "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    "docs/WINDOWS_TUNNEL_PERSISTENCE.md",
    "evidence/acceptance/commands.json",
    "evidence/vendor/implementation_v45/ROW170_VERCEL_PREVIEW_BOUNDARY_RECEIPT.json",
    "evidence/vendor/implementation_v45/ROW180_FULL_LOCAL_VERIFICATION_RECEIPT.json",
    "apps/evidence-lane-app/app/_components/delta-ledger-explorer.tsx",
    "apps/evidence-lane-app/app/_components/source-brain-lab.tsx",
    "apps/evidence-lane-app/app/_data/delta-ledger.ts",
    "apps/evidence-lane-app/app/_data/governed-linked-deltas.ts",
    "apps/evidence-lane-app/app/_data/website-current-execution.ts",
    "apps/evidence-lane-app/app/page.tsx",
    "apps/evidence-lane-app/pnpm-lock.yaml",
    "plugins/evidence-lane-plugin/requirements.lock.txt",
    "plugins/evidence-lane-plugin/requirements.toolchain.lock.txt",
}


def _project_version(path: Path) -> str:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _tracked_text_files() -> list[Path]:
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".runtime",
        ".venv",
        "build",
        "node_modules",
    }
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.split("\0")
    paths = [ROOT / relative for relative in tracked if relative]
    return [
        path
        for path in paths
        if path.is_file()
        and not ignored_parts.intersection(path.relative_to(ROOT).parts)
        and not any(part.endswith(".egg-info") for part in path.relative_to(ROOT).parts)
        and path != ROOT / "tests" / "test_v140_version_consistency.py"
        and path
        != ROOT / "apps" / "evidence-lane-app"
        / "app"
        / "_data"
        / "studio-rag-index.json"
    ]


def test_all_active_codex_product_version_surfaces_are_v300() -> None:
    plugin_manifest = json.loads(
        (
            ROOT
            / "plugins"
            / "evidence-lane-plugin"
            / ".codex-plugin"
            / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    adapter_manifest = json.loads(
        (
            ROOT / "apps" / "evidence-lane-app"
            / "package.json"
        ).read_text(encoding="utf-8")
    )
    assert _project_version(ROOT / "pyproject.toml") == CURRENT_VERSION
    assert (
        _project_version(ROOT / "plugins" / "evidence-lane-plugin" / "pyproject.toml")
        == CURRENT_VERSION
    )
    assert ENGINE_VERSION == CURRENT_VERSION
    assert str(plugin_manifest["version"]).split("+", 1)[0] == CURRENT_VERSION
    assert re.fullmatch(
        r"3\.0\.0\+codex\.\d{14}", str(plugin_manifest["version"])
    )
    assert adapter_manifest["version"] == CURRENT_VERSION


def test_current_codex_docs_and_runtime_surfaces_name_v300() -> None:
    required_fragments = {
        "README.md": [
            "# Evidence Lane",
            "The current Codex source release is **3.0.0**",
            "## 3.0 source and historical compatibility invariants",
        ],
        "ARCHITECTURE.md": [
            "Evidence Lane 3.0.0",
            "## Plugin-maintainer release cycle and downstream projects",
        ],
        "docs/RELEASE_AND_COMPATIBILITY.md": [
            "The current source line is Evidence Lane 3.0.0",
            "Source identity, local package identity, installed-host identity",
        ],
        "docs/HOST_AND_STORAGE_MATRIX.md": [
            "exactly two selectors",
        ],
        "plugins/evidence-lane-plugin/.codex-plugin/plugin.json": [
            "Evidence Lane keeps long Codex projects grounded",
        ],
        "plugins/evidence-lane-plugin/scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1": [
            'transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"',
            "Pinned Evidence Lane $release $SlotRole host-wide secure MCP tunnel.",
        ],
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py": [
            "3.0.0 is the Codex package",
        ],
        "plugins/evidence-lane-plugin/README.md": [
            "# Evidence Lane plugin 3.0.0",
            "Version 3.0.0 is the current governed Codex source release",
        ],
    }

    for relative, fragments in required_fragments.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment in text, f"missing current version text in {relative}: {fragment}"

    stale_active_fragments = {
        "plugins/evidence-lane-plugin/scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1": [
            "Pinned Evidence Lane 1.5.0 host-neutral secure MCP tunnel",
        ],
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py": [
            "1.5.0 is version metadata",
        ],
    }
    for relative, fragments in stale_active_fragments.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment not in text, f"stale active version text in {relative}: {fragment}"


def test_readmes_expose_branding_and_capability_gated_windows_tunnel_setup() -> None:
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    plugin_readme = (
        ROOT / "plugins" / "evidence-lane-plugin" / "README.md"
    ).read_text(encoding="utf-8")

    assert 'src="docs/assets/evidence-lane-full-logo.png"' in root_readme
    assert 'alt="Evidence Lane"' in root_readme
    assert 'src="plugins/evidence-lane-plugin/assets/evidence-lane-icon.png"' in (
        root_readme
    )
    assert 'alt="Evidence Lane plugin icon"' in root_readme

    assert "Bounded Windows tunnel setup" in root_readme
    for fragment in (
        "Install-EvidenceLaneTunnel.ps1",
        "CODEX_APP_INTERACTIVE",
        "HostLifetime Ephemeral",
        "exact-vm-instance-id",
        "Runtime API key",
        "masked",
        "DPAPI",
        "Manage-EvidenceLaneTunnel.ps1",
        "-Action Status",
        "status = PASS",
        "mcp__evidence_lane__*",
        "Headless API",
        "local CLI",
        "host route lacks direct MCP transport",
    ):
        assert fragment in root_readme

    for text in (root_readme, plugin_readme):
        assert "tunnel" in text.lower()
        assert "local" in text.lower()
        assert "does not" in text.lower()
        assert "API key" in text
        assert "masked" in text
        assert "DPAPI" in text

    assert "docs/USER_TUNNEL_GUIDE.md" in root_readme
    assert "../../docs/USER_TUNNEL_GUIDE.md" not in plugin_readme


def test_root_release_configuration_has_no_active_chatgpt_adapter_claims() -> None:
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    for stale in (
        "ChatGPT custom plugins",
        "ChatGPT-only Vercel preview adapter",
        "EVIDENCE_LANE_DURABLE_MCP_ORIGIN",
        "EVIDENCE_LANE_OPENAI_APPS_CHALLENGE_TOKEN",
    ):
        assert stale not in environment

    for stale in (
        "Vercel may host only the thin ChatGPT adapter",
        "thin ChatGPT adapter",
    ):
        assert stale not in security

    assert "package-local native MCP route" in security
    assert re.search(r"Vercel\s+hosts public documentation", security)
    assert "headless/API Streamable" in environment
    assert "HTTP service" in environment
    for name in (
        "EVIDENCE_LANE_GITHUB_APP_CLIENT_ID",
        "EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET",
        "EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET",
        "EVIDENCE_LANE_GITHUB_APP_SESSION_SECRET",
    ):
        assert f"{name}=" in environment
    assert "EVIDENCE_LANE_GITHUB_APP_BASE_URL=https://evidencelane.org" in environment
    assert re.search(
        r"Local\s+Codex and local CLI profiles may require the tunnel", security
    )
    assert "Headless API requests do not require the tunnel" in security


def test_historical_docs_are_excluded_from_public_github_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    receipt = json.loads(
        (
            ROOT
            / "evidence"
            / "implementation_v41"
            / "DELTA080_V130_FINAL_RELEASE_EVIDENCE_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )

    assert "pre-v1.1" not in readme
    assert not (ROOT / "docs" / "DELTA_001_051_TRACEABILITY.md").exists()
    assert not (ROOT / "docs" / "VERSIONING.md").exists()
    assert "Current backend contract" not in (
        ROOT / "docs" / "RELEASE_AND_COMPATIBILITY.md"
    ).read_text(encoding="utf-8")
    assert receipt["schema"] == "evidence-lane.delta080-v130-final-release-evidence-receipt.v1"


def test_v140_release_identity_receipt_is_self_sealed_and_pre_hil() -> None:
    receipt = json.loads(
        (
            ROOT
            / "evidence"
            / "implementation_v43"
            / "V140_RELEASE_IDENTITY_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )
    declared = str(receipt.pop("receipt_sha256"))

    assert declared == sha256_bytes(canonical_json_bytes(receipt))
    # This sealed receipt is immutable historical v1.4.0 evidence. A patch
    # release must not rewrite it to match the moving active product version.
    assert receipt["release"] == "1.4.0"
    assert receipt["base_accepted_pv"] == "PV7"
    assert receipt["base_pointer_generation"] == 7
    assert receipt["base_accepted_commit"] == "42516b2edaae8f37d46523243599650afb2cb5e3"
    assert receipt["publication_boundary"]["main_merge"] == "GATED_UNTIL_FRESH_PV8_APPROVAL"
    assert receipt["safety"]["hil_approval_inferred"] is False


def test_post_pv9_delta_boundary_receipt_is_self_sealed_and_pre_hil() -> None:
    receipt = json.loads(
        (
            ROOT
            / "evidence"
            / "implementation_v44"
            / "V140_POST_PV9_DELTA_BOUNDARY_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )
    declared = str(receipt.pop("receipt_sha256"))

    assert declared == sha256_bytes(canonical_json_bytes(receipt))
    assert receipt["accepted_authority"] == {
        "accepted_manifest_sha256": (
            "54A3FBEBE3DE904AFE694821E5D6ED03D0C271E1C5F319A8017E70DA52FE3C82"
        ),
        "accepted_package_sha256": (
            "A42EF223B1F0FCB1A2FE0A4C1B4F4B463A48D9D2D917D2D34E05F39FF81682A6"
        ),
        "accepted_pv": "PV9",
        "generation": 9,
    }
    assert receipt["next_candidate"]["proposed_pv"] == "PV10"
    assert receipt["chatgpt_contract"]["bundled_skill_count"] == 15
    assert receipt["chatgpt_contract"]["read_tool_count"] == 21
    assert receipt["creative_and_site_contract"]["native_threejs_webgl_site_preserved"]
    assert receipt["creative_and_site_contract"]["meshy_plugin_dependency"] is False
    assert receipt["publication_boundary"]["existing_devpost_project"] == (
        "1348634/evidence_os"
    )
    assert all(value is False for value in receipt["safety"].values())


def test_remaining_v13_occurrences_are_classified() -> None:
    unclassified: list[str] = []
    stale_current_phrases = (
        "active product release is `1.3.0`",
        "single active product release is **1.3.0**",
        "current v1.3 correction standard",
        "current v1.3 implementation",
        "current v1.3 lane bundles",
        "the v1.3 reconciliation gate",
        "evidence lane 1.3.0 separates",
        "evidence lane v1.3 exact-git poc",
    )

    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(ROOT).as_posix()
        lowered = text.casefold()
        for phrase in stale_current_phrases:
            assert phrase not in lowered, f"stale current-release phrase in {relative}: {phrase}"
        if not V13_PATTERN.search(text):
            continue
        if relative.startswith("evidence/implementation_v41/"):
            continue
        if relative not in HISTORICAL_OR_DEPENDENCY_FILES:
            unclassified.append(relative)

    assert not unclassified, f"unclassified v1.3 occurrences: {sorted(unclassified)}"

    rag_index = (
        ROOT
        / "apps"
        / "evidence-lane-app"
        / "app"
        / "_data"
        / "studio-rag-index.json"
    )
    tracked_rag = subprocess.run(
        [
            "git",
            "ls-files",
            "--error-unmatch",
            rag_index.relative_to(ROOT).as_posix(),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    if tracked_rag.returncode != 0:
        return

    browser = json.loads(rag_index.read_text(encoding="utf-8"))
    source_by_id = {source["id"]: source["path"] for source in browser["sources"]}
    bad_chunks = []
    for chunk in browser["chunks"]:
        if not V13_PATTERN.search(chunk["text"]):
            continue
        source = str(source_by_id[chunk["source_id"]]).replace("\\", "/")
        if source.startswith("git/history/"):
            continue
        if source.startswith("evidence/implementation_v41/"):
            continue
        if source not in HISTORICAL_OR_DEPENDENCY_FILES:
            bad_chunks.append(f"{chunk['id']}:{source}")
    assert not bad_chunks, f"studio corpus has unclassified v1.3 chunks: {bad_chunks[:20]}"
