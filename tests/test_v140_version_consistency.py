from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = "2.0.0"
V13_PATTERN = re.compile(r"(?i)(?:\bv1\.3(?:\.0)?\b|\b1\.3\.0\b)")

HISTORICAL_OR_DEPENDENCY_FILES = {
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/CHATGPT_CONNECTION.md",
    "docs/IMPLEMENTATION_TRACEABILITY.md",
    "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    "docs/WINDOWS_TUNNEL_PERSISTENCE.md",
    "evidence/acceptance/commands.json",
    "evidence/implementation_v45/ROW170_VERCEL_PREVIEW_BOUNDARY_RECEIPT.json",
    "evidence/implementation_v45/ROW180_FULL_LOCAL_VERIFICATION_RECEIPT.json",
    "plugins/evidence-lane-plugin/remote_adapter/app/_components/delta-ledger-explorer.tsx",
    "plugins/evidence-lane-plugin/remote_adapter/app/_components/source-brain-lab.tsx",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/delta-ledger.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/governed-linked-deltas.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/website-current-execution.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/page.tsx",
    "plugins/evidence-lane-plugin/remote_adapter/pnpm-lock.yaml",
    "plugins/evidence-lane-plugin/requirements.lock.txt",
    "plugins/evidence-lane-plugin/scripts/windows_tunnel/Manage-EvidenceLaneTunnelVersions.ps1",
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
        != ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "remote_adapter"
        / "app"
        / "_data"
        / "studio-rag-index.json"
    ]


def test_all_active_codex_product_version_surfaces_are_v200() -> None:
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
            ROOT
            / "plugins"
            / "evidence-lane-plugin"
            / "remote_adapter"
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
    assert str(plugin_manifest["version"]).endswith("+codex.20260811150000")
    assert adapter_manifest["version"] == CURRENT_VERSION


def test_current_codex_docs_and_runtime_surfaces_name_v200() -> None:
    required_fragments = {
        "README.md": [
            "# Evidence Lane 2.0.0",
            "The single active Codex product release is **2.0.0**",
            "## v2.0.0 release and historical compatibility invariants",
        ],
        "docs/ARCHITECTURE.md": [
            "Evidence Lane 2.0.0",
            "built v2.0.0 candidates must pass both gates",
        ],
        "docs/VERSIONING.md": [
            "The active Evidence Lane Codex product release is `2.0.0`",
        ],
        "docs/WINDOWS_TUNNEL_PERSISTENCE.md": [
            "starts the v2.0.0 scheduled copy",
        ],
        "plugins/evidence-lane-plugin/.codex-plugin/plugin.json": [
            "2.0.0 is the stable Codex release identity",
        ],
        "plugins/evidence-lane-plugin/scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1": [
            "Pinned Evidence Lane 2.0.0 host-neutral secure MCP tunnel",
        ],
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py": [
            "2.0.0 is the Codex package",
        ],
        "plugins/evidence-lane-plugin/README.md": [
            "# Evidence Lane plugin 2.0.0",
            "Version 2.0.0 is the stable Codex slot",
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


def test_historical_compatibility_and_traceability_versions_are_preserved() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    historical = (ROOT / "docs" / "DELTA_001_051_TRACEABILITY.md").read_text(
        encoding="utf-8"
    )
    versioning = (ROOT / "docs" / "VERSIONING.md").read_text(encoding="utf-8")
    receipt = json.loads(
        (
            ROOT
            / "evidence"
            / "implementation_v41"
            / "DELTA080_V130_FINAL_RELEASE_EVIDENCE_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )

    assert "pre-v1.1" in readme
    assert "v1.1 correction" in historical
    assert "Historical versions remain evidence" in versioning
    assert "third-party dependency versions" in versioning
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

    browser = json.loads(
        (
            ROOT
            / "plugins"
            / "evidence-lane-plugin"
            / "remote_adapter"
            / "app"
            / "_data"
            / "studio-rag-index.json"
        ).read_text(encoding="utf-8")
    )
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
