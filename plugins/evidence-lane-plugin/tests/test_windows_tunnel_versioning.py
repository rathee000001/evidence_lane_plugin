from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    PLUGIN_ROOT / "scripts" / "windows_tunnel" / "Install-EvidenceLaneTunnel.ps1"
)
BOOT = PLUGIN_ROOT / "scripts" / "windows_tunnel" / "EvidenceLaneTunnel.Boot.ps1"
MANAGER = (
    PLUGIN_ROOT / "scripts" / "windows_tunnel" / "Manage-EvidenceLaneTunnel.ps1"
)
SESSION_START = PLUGIN_ROOT / "hooks" / "session_start.py"
TEST_TUNNEL_COMPATIBILITY_SHA256 = "E" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _invoke_installer_functions(functions: list[str], invocation: str) -> dict:
    function_list = ",".join(_ps_literal(name) for name in functions)
    script = f"""
$tokens=$null
$parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
  {_ps_literal(INSTALLER)}, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) {{ throw 'installer parse failed' }}
$wanted=@({function_list})
foreach ($name in $wanted) {{
  $node=@($ast.FindAll({{ param($candidate)
    $candidate -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $candidate.Name -eq $name
  }}, $true))
  if ($node.Count -ne 1) {{ throw "missing function $name" }}
  Invoke-Expression $node[0].Extent.Text
}}
{invocation}
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def _load_session_start():
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_session_start_tunnel_parity_test", SESSION_START
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_installed_fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    version = "3.0.0+codex.20260902183011"
    marketplace = "evidence-lane-v300-testing-new"
    selector = f"evidence-lane-plugin@{marketplace}"
    codex_home = tmp_path / ".codex"
    runtime_control = codex_home / "plugins" / "runtime" / "evidence-lane-plugin"
    plugin_root = (
        codex_home
        / "plugins"
        / "cache"
        / marketplace
        / "evidence-lane-plugin"
        / version
    )
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    surface_path = plugin_root / "manifests" / "executable-surface-registry.v1.json"
    coherence_path = plugin_root / "manifests" / "package" / "package-surface-coherence.json"
    source_manifest_path = plugin_root / "manifests" / "package" / "source-manifest.json"
    tunnel_manifest_path = plugin_root / "tunnel" / "tunnel-manifest.v1.json"
    runner_path = plugin_root / "scripts" / "run_mcp.py"
    for path in (
        manifest_path,
        surface_path,
        coherence_path,
        source_manifest_path,
        tunnel_manifest_path,
        runner_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"name": "evidence-lane-plugin", "version": version}),
        encoding="utf-8",
    )
    surface_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.executable-package-surface-registry.v1",
                "status": "PASS",
                "plugin_version": version,
                "members": [],
            }
        ),
        encoding="utf-8",
    )
    runner_path.write_text("print('bound runner')\n", encoding="utf-8")
    tunnel_manifest_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.installed-tunnel-surface.v1",
                "status": "PASS",
                "tunnel_compatibility_schema": (
                    "evidence-lane.tunnel-capability-compatibility.v1"
                ),
                "tunnel_compatibility_sha256": (
                    TEST_TUNNEL_COMPATIBILITY_SHA256
                ),
            }
        ),
        encoding="utf-8",
    )
    coherence_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.package-surface-coherence.v1",
                "status": "PASS",
                "plugin_version": version,
                "executable_surface_registry_sha256": _sha256(surface_path),
                "receipt_sha256": "A" * 64,
            }
        ),
        encoding="utf-8",
    )
    source_manifest_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.non-lifecycle-local-package-rehearsal.v1.source-manifest",
                "members": [
                    {
                        "path": ".codex-plugin/plugin.json",
                        "sha256": _sha256(manifest_path),
                    },
                    {
                        "path": "manifests/executable-surface-registry.v1.json",
                        "sha256": _sha256(surface_path),
                    },
                    {"path": "scripts/run_mcp.py", "sha256": _sha256(runner_path)},
                    {
                        "path": "tunnel/tunnel-manifest.v1.json",
                        "sha256": _sha256(tunnel_manifest_path),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt_dir = runtime_control / "installations" / "codex-v300"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "INSTALL_FIXTURE_PLUGIN_CREATOR_LOCAL_RESTART.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.codex-stable-installation.v2",
                "status": "PASS",
                "plugin": {
                    "plugin_id": "evidence-lane-plugin",
                    "version": version,
                    "manifest_sha256": _sha256(manifest_path),
                    "package_proofs": {
                        "status": "PASS",
                        "records": [
                            {
                                "path": "manifests/package/source-manifest.json",
                                "sha256": _sha256(source_manifest_path),
                            },
                            {
                                "path": "manifests/package/package-surface-coherence.json",
                                "sha256": _sha256(coherence_path),
                            },
                        ],
                    },
                },
                "activation": {
                    "state": "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED",
                    "plugin_selector": selector,
                    "installed_path": str(plugin_root.resolve()),
                },
                "marketplace": {"name": marketplace},
                "archive_sha256": "B" * 64,
                "package_receipt_sha256": "C" * 64,
                "receipt_sha256": "D" * 64,
            }
        ),
        encoding="utf-8",
    )
    return plugin_root, runtime_control, marketplace, selector


def test_version_identity_uses_digest_to_prevent_sanitized_collisions() -> None:
    first = _invoke_installer_functions(
        ["Get-StringSha256", "Get-VersionedTunnelIdentity"],
        "Get-VersionedTunnelIdentity -Release '3.0.0' "
        "-SlotRole 'versioned-local-testing' "
        "-PluginVersion '3.0.0+codex.CASE' "
        f"-TunnelCompatibilitySha256 '{TEST_TUNNEL_COMPATIBILITY_SHA256}' "
        "| ConvertTo-Json -Compress",
    )
    second = _invoke_installer_functions(
        ["Get-StringSha256", "Get-VersionedTunnelIdentity"],
        "Get-VersionedTunnelIdentity -Release '3.0.0' "
        "-SlotRole 'versioned-local-testing' "
        "-PluginVersion '3.0.0+codex.case' "
        f"-TunnelCompatibilitySha256 '{TEST_TUNNEL_COMPATIBILITY_SHA256}' "
        "| ConvertTo-Json -Compress",
    )
    assert first["plugin_version_token"] == second["plugin_version_token"]
    assert first["plugin_version_digest"] != second["plugin_version_digest"]
    assert first["tunnel_version_token"] == second["tunnel_version_token"]


def test_tunnel_identity_rotates_only_when_capabilities_change() -> None:
    first = _invoke_installer_functions(
        ["Get-StringSha256", "Get-VersionedTunnelIdentity"],
        "Get-VersionedTunnelIdentity -Release '3.0.0' "
        "-SlotRole 'versioned-local-testing' "
        "-PluginVersion '3.0.0+codex.CASE' "
        f"-TunnelCompatibilitySha256 '{TEST_TUNNEL_COMPATIBILITY_SHA256}' "
        "| ConvertTo-Json -Compress",
    )
    second_compatibility = "F" * 64
    second = _invoke_installer_functions(
        ["Get-StringSha256", "Get-VersionedTunnelIdentity"],
        "Get-VersionedTunnelIdentity -Release '3.0.0' "
        "-SlotRole 'versioned-local-testing' "
        "-PluginVersion '3.0.0+codex.CASE' "
        f"-TunnelCompatibilitySha256 '{second_compatibility}' "
        "| ConvertTo-Json -Compress",
    )
    assert first["plugin_version_digest"] == second["plugin_version_digest"]
    assert first["tunnel_version_token"] != second["tunnel_version_token"]


def test_installer_and_session_start_version_identity_are_exactly_equal(
    tmp_path: Path,
) -> None:
    installer_identity = _invoke_installer_functions(
        ["Get-StringSha256", "Get-VersionedTunnelIdentity"],
        "Get-VersionedTunnelIdentity -Release '3.0.0' "
        "-SlotRole 'versioned-local-testing' "
        "-PluginVersion '3.0.0+codex.20260902183011' "
        f"-TunnelCompatibilitySha256 '{TEST_TUNNEL_COMPATIBILITY_SHA256}' "
        "| ConvertTo-Json -Compress",
    )
    session_start = _load_session_start()
    session_identity = session_start._version_bound_tunnel_identity(
        release="3.0.0",
        slot_role="versioned-local-testing",
        plugin_version="3.0.0+codex.20260902183011",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=tmp_path,
    )
    for field in (
        "release_token",
        "plugin_version_token",
        "plugin_version_sha256",
        "plugin_version_digest",
        "tunnel_version_token",
        "file_prefix",
        "profile_name",
        "task_name",
    ):
        assert session_identity[field] == installer_identity[field]


def test_installed_binding_rejects_tampered_executable_surface(tmp_path: Path) -> None:
    plugin_root, runtime_control, marketplace, selector = _write_installed_fixture(
        tmp_path
    )
    surface = plugin_root / "manifests" / "executable-surface-registry.v1.json"
    surface.write_text(surface.read_text(encoding="utf-8") + " ", encoding="utf-8")
    invocation = (
        "Get-SealedInstalledPluginBinding "
        f"-ExactPluginRoot {_ps_literal(plugin_root)} "
        "-PluginVersion '3.0.0+codex.20260902183011' "
        f"-MarketplaceName {_ps_literal(marketplace)} "
        f"-ExpectedSelector {_ps_literal(selector)} "
        f"-ExactRuntimeControlRoot {_ps_literal(runtime_control)} "
        "| ConvertTo-Json -Compress"
    )
    try:
        _invoke_installer_functions(
            ["Get-PathSha256", "Get-SealedInstalledPluginBinding"], invocation
        )
    except RuntimeError as exc:
        assert "do not reconcile" in str(exc)
    else:
        raise AssertionError("tampered executable surface was accepted")


def test_installed_binding_accepts_one_exact_sealed_cache(tmp_path: Path) -> None:
    plugin_root, runtime_control, marketplace, selector = _write_installed_fixture(
        tmp_path
    )
    binding = _invoke_installer_functions(
        ["Get-PathSha256", "Get-SealedInstalledPluginBinding"],
        "Get-SealedInstalledPluginBinding "
        f"-ExactPluginRoot {_ps_literal(plugin_root)} "
        "-PluginVersion '3.0.0+codex.20260902183011' "
        f"-MarketplaceName {_ps_literal(marketplace)} "
        f"-ExpectedSelector {_ps_literal(selector)} "
        f"-ExactRuntimeControlRoot {_ps_literal(runtime_control)} "
        "| ConvertTo-Json -Compress",
    )
    assert binding["status"] == "PASS"
    assert binding["selector"] == selector
    assert Path(binding["installed_cache_root"]) == plugin_root.resolve()


def test_installed_binding_rejects_stale_selector(tmp_path: Path) -> None:
    plugin_root, runtime_control, marketplace, _selector = _write_installed_fixture(
        tmp_path
    )
    invocation = (
        "Get-SealedInstalledPluginBinding "
        f"-ExactPluginRoot {_ps_literal(plugin_root)} "
        "-PluginVersion '3.0.0+codex.20260902183011' "
        f"-MarketplaceName {_ps_literal(marketplace)} "
        "-ExpectedSelector 'evidence-lane-plugin@stale-selector' "
        f"-ExactRuntimeControlRoot {_ps_literal(runtime_control)} "
        "| ConvertTo-Json -Compress"
    )
    try:
        _invoke_installer_functions(
            ["Get-PathSha256", "Get-SealedInstalledPluginBinding"], invocation
        )
    except RuntimeError as exc:
        assert "one exact sealed installed-selector receipt" in str(exc)
    else:
        raise AssertionError("stale selector receipt was accepted")


def test_rotation_contract_restores_prior_and_disables_failed_new_version() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    activation = source[source.index("if ($Activate) {") :]
    assert "Get-ActivePriorTunnelBindings" in activation
    assert "Invoke-RetainedTunnelManager" in activation
    assert "-Action Start" in activation
    assert "Disable-ScheduledTask -TaskName $TaskName" in activation
    assert "NEW_LOCAL_TUNNEL_FAILED_PRIOR_RESTORED" in activation
    assert 'prior_local_tunnel_rollback_status = "PASS"' in activation
    assert activation.count("Assert-SingleActiveTunnel") >= 2


def test_generated_tunnel_manifest_is_capability_bound_and_project_neutral() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / "tunnel" / "tunnel-manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "PASS"
    assert manifest["tunnel_compatibility_schema"] == (
        "evidence-lane.tunnel-capability-compatibility.v1"
    )
    assert len(manifest["tunnel_compatibility_sha256"]) == 64
    assert manifest["tunnel_rebuild_trigger"] == (
        "CAPABILITY_FINGERPRINT_CHANGED_ONLY"
    )
    assert manifest["exact_plugin_rebind_required_every_install"] is True
    assert manifest["compatible_runtime_key_and_prewarm_reused"] is True
    assert manifest["runtime_key_prompt_policy"] == (
        "FIRST_REGISTRATION_OR_MISSING_INVALID_CREDENTIAL_ONLY"
    )
    assert manifest["host_wide_project_neutral"] is True
    assert manifest["per_project_or_task_tunnel_allowed"] is False
    assert manifest["routes_by_project_id"] is True


def test_boot_and_manager_rehash_bound_installed_surfaces() -> None:
    for script in (BOOT, MANAGER):
        source = script.read_text(encoding="utf-8")
        assert "Assert-InstalledCacheBinding" in source
        assert "executable_surface_registry_sha256" in source
        assert "package_surface_coherence_sha256" in source
        assert "installed_receipt_file_sha256" in source
        assert "installed_runner_sha256" in source
