from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from evidence_lane_plugin.engine_identity import toolchain_manifest
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.search_toolchain import (
    INVOCATION_SCHEMA,
    MANIFEST_SCHEMA,
    SearchToolchainError,
    bounded_text_search,
    declared_search_toolchain_identity,
    load_search_toolchain_manifest,
    plugin_root,
    resolve_search_tool,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
MANIFEST = PLUGIN / "toolchains" / "search-tools.v1.json"


def _fallback_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugin contract only"
    target = root / "toolchains" / MANIFEST.name
    target.parent.mkdir(parents=True)
    target.write_bytes(MANIFEST.read_bytes())
    return root


def test_search_toolchain_manifest_seals_binaries_licenses_and_fallbacks() -> None:
    manifest = load_search_toolchain_manifest(PLUGIN)
    identity = declared_search_toolchain_identity(PLUGIN)

    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["scope"] == "ALL_GOVERNED_PROJECTS"
    assert manifest["resolution_order"] == [
        "PACKAGE_LOCAL_VERIFIED_BINARY",
        "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY",
        "DETERMINISTIC_BUILTIN_FALLBACK",
    ]
    assert manifest["auto_download_during_mcp_handshake"] is False
    assert manifest["path_lookup_allowed"] is False
    assert manifest["shell_execution_allowed"] is False
    assert [row["tool_id"] for row in manifest["tools"]] == ["ripgrep"]
    assert manifest["fts_authority"] == {
        "backend": "SQLITE_FTS5",
        "query_mode": "BOUNDED_FTS5",
        "scope": "PLAN_LANE_CHATLINEAGE_AND_PROJECT_SECTORS",
        "pointer_and_locator_required": True,
        "model_context_policy": "BOUNDED_QUERY_RESULTS_ONLY",
        "pv_package_loaded_into_model_context": False,
        "fallback": "FAIL_CLOSED_WHEN_SQLITE_FTS5_UNAVAILABLE",
    }
    assert identity["fts_authority"] == manifest["fts_authority"]
    assert all(row["package_binary_present"] for row in identity["binaries"])
    assert all(row["package_binary_matches"] for row in identity["binaries"])
    assert all(row["license_files_present"] for row in identity["binaries"])
    assert len(identity["manifest_sha256"]) == 64
    assert len(identity["identity_sha256"]) == 64

    engine_tools = toolchain_manifest()["external_search_tools"]
    assert engine_tools["manifest_sha256"] == identity["manifest_sha256"]
    assert engine_tools["identity_sha256"] == identity["identity_sha256"]


def test_explicit_plugin_root_supports_installed_runtime_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EVIDENCE_LANE_PLUGIN_ROOT", str(PLUGIN))
    assert plugin_root() == PLUGIN.resolve()

    invalid = tmp_path / "not a plugin"
    invalid.mkdir()
    monkeypatch.setenv("EVIDENCE_LANE_PLUGIN_ROOT", str(invalid))
    with pytest.raises(SearchToolchainError, match="PLUGIN_ROOT_INVALID"):
        plugin_root()

    monkeypatch.setenv("EVIDENCE_LANE_PLUGIN_ROOT", "relative/plugin")
    with pytest.raises(SearchToolchainError, match="PLUGIN_ROOT_INVALID"):
        plugin_root()


@pytest.mark.skipif(os.name != "nt", reason="packaged executable is Windows x64")
def test_package_local_rg_is_exact_version_and_hash_verified() -> None:
    expected = {
        "ripgrep": "14231169855EC5205CF5A1B6F1DB358FF4AED4247C86B69CE8AAE647C77F6680",
    }
    for tool_id, digest in expected.items():
        resolution = resolve_search_tool(tool_id, root=PLUGIN)
        receipt = resolution.receipt()
        assert resolution.backend == "PACKAGE_LOCAL_VERIFIED_BINARY"
        assert resolution.binary_sha256 == digest
        assert resolution.executable is not None
        assert sha256_file(resolution.executable) == digest
        assert receipt["raw_executable_path_included"] is False
        assert receipt["path_lookup_used"] is False
        assert receipt["auto_download_used"] is False


@pytest.mark.skipif(os.name != "nt", reason="configured executable is Windows x64")
def test_explicit_host_binary_requires_hash_and_supports_paths_with_spaces(
    tmp_path: Path,
) -> None:
    fallback_root = _fallback_root(tmp_path)
    configured = tmp_path / "configured tools with spaces" / "rg.exe"
    configured.parent.mkdir(parents=True)
    shutil.copy2(
        PLUGIN / "toolchains" / "bin" / "windows-x86_64" / "rg.exe",
        configured,
    )
    digest = sha256_file(configured)

    missing_hash = resolve_search_tool(
        "ripgrep",
        root=fallback_root,
        configured_path=configured,
    )
    assert missing_hash.backend == "DETERMINISTIC_BUILTIN_FALLBACK"
    assert "CONFIGURED_BINARY_SHA256_REQUIRED" in missing_hash.reason

    verified = resolve_search_tool(
        "ripgrep",
        root=fallback_root,
        configured_path=configured,
        configured_sha256=digest,
    )
    assert verified.backend == "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY"
    assert verified.binary_sha256 == digest
    assert verified.receipt()["raw_executable_path_included"] is False


def test_missing_corrupt_wrong_platform_and_windowsapps_routes_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_root = _fallback_root(tmp_path)
    unavailable = resolve_search_tool("ripgrep", root=fallback_root)
    assert unavailable.backend == "DETERMINISTIC_BUILTIN_FALLBACK"

    windowsapps = tmp_path / "WindowsApps" / "rg.exe"
    windowsapps.parent.mkdir()
    windowsapps.write_bytes(b"not executable")
    rejected = resolve_search_tool(
        "ripgrep",
        root=fallback_root,
        configured_path=windowsapps,
        configured_sha256="A" * 64,
    )
    assert rejected.backend == "DETERMINISTIC_BUILTIN_FALLBACK"
    if os.name == "nt":
        assert "HOST_BINARY_UNTRUSTED_OWNERSHIP_ROOT" in rejected.reason

    monkeypatch.setattr(
        "evidence_lane_plugin.search_toolchain._platform_id",
        lambda: "unsupported-test-platform",
    )
    cross_platform = resolve_search_tool("ripgrep", root=PLUGIN)
    assert cross_platform.backend == "DETERMINISTIC_BUILTIN_FALLBACK"
    assert "PACKAGE_BINARY_PLATFORM_UNAVAILABLE" in cross_platform.reason

    with pytest.raises(SearchToolchainError, match="SEARCH_TOOL_UNKNOWN"):
        resolve_search_tool("fzf", root=PLUGIN)
    assert not (PLUGIN / "toolchains" / "bin" / "windows-x86_64" / "fzf.exe").exists()


def test_rg_search_and_python_fallback_are_bounded_stable_and_secret_safe(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project with spaces"
    (project / "src").mkdir(parents=True)
    (project / "src" / "b.txt").write_text("needle second\n", encoding="utf-8")
    (project / "src" / "a.txt").write_text(
        "needle api_key=SECRET_VALUE_1234567890\n",
        encoding="utf-8",
    )
    (project / ".env").write_text("needle=SECRET\n", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "ignored.txt").write_text(
        "needle ignored\n",
        encoding="utf-8",
    )

    native = bounded_text_search(project, "needle", limit=10, plugin_source_root=PLUGIN)
    fallback = bounded_text_search(
        project,
        "needle",
        limit=10,
        plugin_source_root=_fallback_root(tmp_path),
    )
    assert [row["path"] for row in native["results"]] == ["src/a.txt", "src/b.txt"]
    assert [row["path"] for row in fallback["results"]] == [
        "src/a.txt",
        "src/b.txt",
    ]
    assert "SECRET_VALUE" not in json.dumps(native)
    assert "SECRET_VALUE" not in json.dumps(fallback)
    assert native["receipt"]["schema"] == INVOCATION_SCHEMA
    assert native["receipt"]["secret_paths_excluded"] is True
    assert native["receipt"]["source_mutated"] is False
    assert native["receipt"]["git_mutated"] is False
    assert fallback["receipt"]["capability"]["backend"] == (
        "DETERMINISTIC_BUILTIN_FALLBACK"
    )


def test_query_bound_is_enforced_without_fuzzy_selector(tmp_path: Path) -> None:
    with pytest.raises(SearchToolchainError, match="QUERY_BOUND"):
        bounded_text_search(tmp_path, "x" * 1025, plugin_source_root=PLUGIN)
