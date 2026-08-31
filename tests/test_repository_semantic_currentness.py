from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "audit_repository_semantic_currentness.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "audit_repository_semantic_currentness", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stale_active_route_and_dated_currentness_are_not_allowlisted() -> None:
    module = _module()
    stale, allowed = module._identity_findings(
        "plugins/evidence-lane-plugin/src/evidence_lane_plugin/runtime.py",
        "authority = 'codex-v200'\n# Evidence Lane current-route refresh: 3.0.0 / 2026-08-23  # historical negative proof\n",
    )
    assert allowed == []
    assert any(value.startswith("STALE_ROUTE_TOKEN:") for value in stale)
    assert any(value.startswith("DATED_CURRENTNESS_CLAIM:") for value in stale)


def test_explicit_historical_negative_proof_is_classified_not_promoted() -> None:
    module = _module()
    stale, allowed = module._identity_findings(
        "tests/test_history_compatibility.py",
        "assert 'codex-v200' not in current_runtime  # historical negative proof\n",
    )
    assert stale == []
    assert len(allowed) == 1


def test_plugin_relative_file_and_directory_references_resolve() -> None:
    module = _module()
    paths = {
        "plugins/evidence-lane-plugin/scripts/run_mcp.py",
        "plugins/evidence-lane-plugin/authorities/project_memory/manifest.v1.json",
    }
    value = {
        "path": "authorities/project_memory",
        "schema_path": "scripts/run_mcp.py",
    }
    assert module._json_reference_findings(
        value, paths, "plugins/evidence-lane-plugin/"
    ) == []


def test_final_semantic_classifications_are_exact_and_closed() -> None:
    module = _module()
    assert module.FINAL_CLASSIFICATIONS == {
        "CURRENT",
        "HISTORICAL_ALLOWED_WITH_BOUNDARY",
        "NONSEMANTIC_BINARY_VERIFIED",
        "STALE_PURGED",
    }
