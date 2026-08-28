from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.code_toolchain import CODE_TOOLCHAIN_LANGUAGES
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file
from evidence_lane_plugin.native_toolchain import (
    NATIVE_MANIFEST_SCHEMA,
    NATIVE_POINTER_SCHEMA,
    native_manifest,
    resolve_native_tool,
    validate_hidden_runtime_root,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _installer_module():
    path = PLUGIN / "scripts" / "codex_release" / "install_native_toolchain.py"
    spec = importlib.util.spec_from_file_location("install_native_toolchain", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_manifest_is_hidden_runtime_and_license_complete() -> None:
    manifest = native_manifest(PLUGIN)
    assert manifest["schema"] == NATIVE_MANIFEST_SCHEMA
    assert manifest["host_profiles"] == ["CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"]
    assert manifest["install_scope"] == "HIDDEN_CODEX_PLUGIN_RUNTIME_ONLY"
    assert manifest["workspace_install_allowed"] is False
    assert manifest["path_mutation_allowed"] is False
    rows = {row["tool_id"]: row for row in manifest["tools"]}
    assert set(rows) == {
        "jq",
        "graphviz",
        "poppler",
        "tesseract",
        "ghostscript",
        "ffmpeg",
        "ripgrep",
        "seven_zip_extractor",
    }
    assert rows["ghostscript"]["default_acquisition_allowed"] is False
    assert rows["ghostscript"]["license_grant_reference_required"] is True
    assert rows["tesseract"]["kind"] == "portable_nsis_extract"
    assert rows["tesseract"]["extractor_tool_id"] == "seven_zip_extractor"
    assert len(rows["tesseract"]["extractor_sha256"]) == 64
    assert rows["ghostscript"]["installer_family"] == "nsis"
    for tool_id in ("jq", "graphviz", "poppler", "tesseract", "ghostscript"):
        assert len(rows[tool_id]["license"]["sha256"]) == 64


def test_runtime_root_rejects_workspace_and_accepts_hidden_shape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HIDDEN_RUNTIME_ROOT_REQUIRED"):
        validate_hidden_runtime_root(tmp_path / "workspace")
    hidden = tmp_path / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
    assert validate_hidden_runtime_root(hidden) == hidden.resolve()


def test_native_pointer_resolves_only_hash_bound_hidden_executable(tmp_path: Path) -> None:
    hidden = tmp_path / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
    executable = hidden / "toolchains" / "jq" / "1.8.2" / "bin" / "jq.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic-test-executable")
    core = {
        "schema": NATIVE_POINTER_SCHEMA,
        "status": "PASS",
        "tools": [
            {
                "tool_id": "jq",
                "status": "PASS",
                "version": "1.8.2",
                "executable": str(executable),
                "executable_sha256": sha256_file(executable),
                "license_receipt_sha256": "A" * 64,
            }
        ],
    }
    pointer = hidden / "toolchains" / "CURRENT_NATIVE_TOOLCHAIN.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    resolved = resolve_native_tool("jq", runtime_root=hidden)
    assert resolved.executable == executable.resolve()
    executable.write_bytes(b"drift")
    with pytest.raises(RuntimeError, match="HASH_MISMATCH"):
        resolve_native_tool("jq", runtime_root=hidden)


def test_native_zip_extraction_rejects_path_escape(tmp_path: Path) -> None:
    module = _installer_module()
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("prefix/../../escape.txt", "no")
    with pytest.raises(module.InstallError, match="PATH_UNSAFE"):
        module.safe_extract(archive, tmp_path / "out", "prefix/")


def test_drain_route_is_absent_and_restart_preparation_has_no_process_control() -> None:
    drain = PLUGIN / "scripts" / "codex_release" / "drain_codex_task_turns.py"
    prepare = (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Prepare-EvidenceLaneCodexRestart.ps1"
    ).read_text(encoding="utf-8")
    assert not drain.exists()
    assert "drain_utility_allowed = $false" in prepare
    assert "programmatic_process_stop_allowed = $false" in prepare
    assert "Stop-Process" not in prepare
    assert "ScheduledTask" not in prepare


def test_local_update_prewarm_installs_native_toolchain_before_probe() -> None:
    installer = (
        PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py"
    ).read_text(encoding="utf-8")
    native_install = installer.index('"install_native_toolchain.py"')
    runtime_probe = installer.index('probe_source = (', native_install)
    assert native_install < runtime_probe
    assert 'environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"]' in installer
    assert 'environment["EVIDENCE_LANE_HOST_PROFILE"] = "CODEX_DESKTOP"' in installer
    assert '"native_toolchain_receipt_sha256"' in installer


def test_hidden_runtime_provisioning_pins_grammars_model_and_tunnel_prewarm() -> None:
    native_installer = (
        PLUGIN / "scripts" / "codex_release" / "install_native_toolchain.py"
    ).read_text(encoding="utf-8")
    tunnel_installer = (
        PLUGIN
        / "scripts"
        / "windows_tunnel"
        / "Install-EvidenceLaneTunnel.ps1"
    ).read_text(encoding="utf-8")

    assert len(CODE_TOOLCHAIN_LANGUAGES) == 33
    assert "c_sharp" in CODE_TOOLCHAIN_LANGUAGES
    assert '"csharp" if language == "c_sharp" else language' in native_installer
    assert "TREE_SITTER_LANGUAGE_PREFETCH_NETWORK_GRANT_REQUIRED" in native_installer
    assert "prefetch(grammar_download_ids)" in native_installer
    assert '"download_and_runtime_aliases_distinct": True' in native_installer
    assert '"runtime_auto_download_allowed": False' in native_installer
    assert '"local_update_prefetch": True' in native_installer

    assert 'repository = "BAAI/bge-small-en-v1.5"' in native_installer
    assert (
        'revision = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"'
        in native_installer
    )
    assert "local_files_only=not allow_network" in native_installer
    assert "SentenceTransformer(str(target), local_files_only=True)" in native_installer
    assert '"dimension": 384' in native_installer
    assert '"runtime_network_allowed": False' in native_installer
    assert '"model_path": str(target.resolve())' in native_installer

    assert "--prewarm-only" in tunnel_installer
    assert "evidence-lane.runtime-toolchain-prewarm.v1" in tunnel_installer
    assert "$prewarm.runtime_toolchain.failure_count -ne 0" in tunnel_installer
    assert "all_required_tunnel_dependencies_prewarmed" in tunnel_installer
    assert "workspace" not in tunnel_installer.lower().split(
        "all_required_tunnel_dependencies_prewarmed", 1
    )[1][:400]


def test_all_tool_requirements_are_tunnel_linked_and_license_classified() -> None:
    matrix = json.loads(
        (PLUGIN / "toolchains" / "tool-requirement-matrix.v1.json").read_text(
            encoding="utf-8"
        )
    )
    tunnel = json.loads(
        (PLUGIN / "toolchains" / "tunnel-runtime-toolchain.v1.json").read_text(
            encoding="utf-8"
        )
    )
    licenses = json.loads(
        (PLUGIN / "toolchains" / "tool-license-inventory.v1.json").read_text(
            encoding="utf-8"
        )
    )
    matrix_tools = {row["tool"] for row in matrix["requirements"]}
    assert tunnel["requirement_count"] == len(matrix_tools)
    assert tunnel["all_tool_requirements_linked"] is True
    assert {row["tool"] for row in tunnel["requirements"]} == matrix_tools
    assert licenses["tool_requirement_count"] == len(matrix_tools)
    assert licenses["all_tool_requirements_classified"] is True
    assert licenses["mcp_inventory_separate"] is True
    assert {row["tool"] for row in licenses["rows"]} == matrix_tools
