from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "audit_repository_semantic_currentness.py"
)
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SHARED_BOUNDARIES = (
    PLUGIN
    / "skills"
    / "evidence-lane-code-lifecycle"
    / "references"
    / "shared-boundaries.md"
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


def test_semantic_audit_requires_one_exact_codex_cachebuster() -> None:
    module = _module()

    assert module.EXACT_PLUGIN_VERSION_RE.fullmatch(
        "3.0.0+codex.20260902183011"
    )
    assert module.EXACT_PLUGIN_VERSION_RE.fullmatch("3.0.0") is None
    assert (
        module.EXACT_PLUGIN_VERSION_RE.fullmatch(
            "3.0.0+codex.first+codex.second"
        )
        is None
    )


def _json(relative: str) -> dict:
    return json.loads((PLUGIN / relative).read_text(encoding="utf-8"))


def test_tool_requirement_count_is_current_registry_derived_and_cross_surface_equal() -> None:
    module = _module()
    parity = module._tool_requirement_registry_parity(
        tool_matrix=_json("toolchains/tool-requirement-matrix.v1.json"),
        toolchain_surface=_json("toolchains/toolchain-surface.v1.json"),
        tunnel_toolchain=_json("toolchains/tunnel-runtime-toolchain.v1.json"),
        license_inventory=_json("toolchains/tool-license-inventory.v1.json"),
        workflow_pairing=_json("toolchains/unified-tool-workflow-pairing.v1.json"),
        mcp_manifest=_json("mcp/mcp-manifest.v1.json"),
    )

    assert parity["current_count"] == 119
    assert parity["ordered_tool_sets_equal"] is True
    assert parity["counts_equal"] is True
    assert parity["cross_surface_equal"] is True
    assert parity["count_is_registry_snapshot_not_ceiling"] is True
    assert (
        '"tool_requirement_registry": tool_requirement_registry'
        in SCRIPT.read_text(encoding="utf-8")
    )


def test_tool_requirement_count_fails_closed_on_cross_surface_drift() -> None:
    module = _module()
    mcp_manifest = _json("mcp/mcp-manifest.v1.json")
    mcp_manifest["tool_requirement_count"] += 1

    parity = module._tool_requirement_registry_parity(
        tool_matrix=_json("toolchains/tool-requirement-matrix.v1.json"),
        toolchain_surface=_json("toolchains/toolchain-surface.v1.json"),
        tunnel_toolchain=_json("toolchains/tunnel-runtime-toolchain.v1.json"),
        license_inventory=_json("toolchains/tool-license-inventory.v1.json"),
        workflow_pairing=_json("toolchains/unified-tool-workflow-pairing.v1.json"),
        mcp_manifest=mcp_manifest,
    )

    assert parity["current_count"] == 119
    assert parity["counts_equal"] is False
    assert parity["cross_surface_equal"] is False


def test_tool_requirement_parity_accepts_future_registry_size_without_source_constant() -> None:
    module = _module()
    tools = ["tool-a", "tool-b", "tool-c"]

    def rows() -> list[dict[str, str]]:
        return [{"tool": tool} for tool in tools]

    parity = module._tool_requirement_registry_parity(
        tool_matrix={"requirements": rows()},
        toolchain_surface={"requirement_count": len(tools)},
        tunnel_toolchain={
            "requirement_count": len(tools),
            "full_matrix_requirement_count": len(tools),
            "requirements": rows(),
        },
        license_inventory={
            "tool_requirement_count": len(tools),
            "license_classification_count": len(tools),
            "rows": rows(),
        },
        workflow_pairing={"tool_count": len(tools), "rows": rows()},
        mcp_manifest={"tool_requirement_count": len(tools)},
    )

    assert parity["current_count"] == len(tools)
    assert parity["cross_surface_equal"] is True
    source = SCRIPT.read_text(encoding="utf-8")
    assert "== 119" not in source
    assert "== 120" not in source


def test_shared_boundaries_define_registry_snapshots_without_numeric_ceiling() -> None:
    text = SHARED_BOUNDARIES.read_text(encoding="utf-8")

    assert "Counts are current registry snapshots, never ceilings." in text
    assert "derived\n  from their separate canonical registries at audit time" in text
    assert "capability matrix has 120 entries" not in text


def test_publication_deferral_contract_is_exact_and_never_authorizes_publish() -> None:
    module = _module()
    contract = ROOT / "contracts" / "task35-publication-deferral.v1.json"

    result = module._publication_deferral(contract)

    assert result["status"] == "DEFERRED_NOT_PASSED"
    assert result["publication_authorized"] is False
    assert result["documentation_generation_authorized"] is False
    assert len(result["selectors"]) == 5
    assert result["surfaces"] == [
        "GITHUB_DOCUMENTATION",
        "GITHUB_PAGES",
        "VERCEL_PUBLICATION",
    ]
    assert len(result["contract_file_sha256"]) == 64
