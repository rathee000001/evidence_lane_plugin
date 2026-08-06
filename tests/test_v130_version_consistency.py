from __future__ import annotations

import json
import tomllib
from pathlib import Path

from evidence_lane_plugin.constants import ENGINE_VERSION

ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = "1.3.0"


def _project_version(path: Path) -> str:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def test_all_active_product_version_surfaces_are_v130() -> None:
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
    assert adapter_manifest["version"] == CURRENT_VERSION


def test_current_docs_site_poc_and_acceptance_surfaces_name_v13() -> None:
    required_fragments = {
        "README.md": [
            "# Evidence Lane Plugin 1.3.0",
            "The single active product release is **1.3.0**",
            "The v1.3 reconciliation gate",
            "Current v1.3 lane bundles",
        ],
        "docs/ARCHITECTURE.md": [
            "Evidence Lane 1.3.0",
            "built v1.3 candidates must pass both gates",
        ],
        "docs/VERSIONING.md": [
            "The active Evidence Lane product release is `1.3.0`",
        ],
        "plugins/evidence-lane-plugin/remote_adapter/app/proof/page.tsx": [
            "Current v1.3 correction standard",
        ],
        "plugins/evidence-lane-plugin/remote_adapter/app/provenance/page.tsx": [
            "current v1.3 implementation",
        ],
        "plugins/evidence-lane-plugin/scripts/build_real_git_poc.py": [
            "Evidence Lane v1.3 exact-Git PoC",
            "Evidence Lane v1.3 exact plugin commit",
        ],
        "plugins/evidence-lane-plugin/scripts/run_acceptance_check.py": [
            "Evidence Lane v1.3 acceptance check",
        ],
    }
    stale_current_phrases = (
        "The v1.1 reconciliation gate",
        "New v1.1 lane bundles",
        "built v1.1 candidates",
        "Current v1.1 correction standard",
        "prove the v1.1 implementation",
        "Evidence Lane v1.2 exact-Git PoC",
        "Evidence Lane v1.2 exact plugin commit",
        "evidence-lane-v1.2-delta063",
    )

    for relative, fragments in required_fragments.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment in text, f"missing current version text in {relative}: {fragment}"
        for stale in stale_current_phrases:
            assert stale not in text, f"stale active version text in {relative}: {stale}"


def test_historical_compatibility_and_traceability_versions_are_preserved() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    historical = (ROOT / "docs" / "DELTA_001_051_TRACEABILITY.md").read_text(
        encoding="utf-8"
    )
    versioning = (ROOT / "docs" / "VERSIONING.md").read_text(encoding="utf-8")

    assert "pre-v1.1" in readme
    assert "v1.1 correction" in historical
    assert "Historical versions remain evidence" in versioning
    assert "third-party dependency versions" in versioning
