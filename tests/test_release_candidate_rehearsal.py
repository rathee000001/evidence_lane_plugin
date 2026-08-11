from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "evidence-lane-plugin" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_release_candidate_rehearsal import (
    BOUNDARY,
    PackageBoundaryError,
    build_rehearsal,
)

VERSION = "2.0.0+codex.20260811030012"
COMMIT = "a" * 40
TREE = "b" * 40


def _write(root: Path, relative: str, content: bytes | str = b"fixture") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(content)


def _plugin_fixture(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    _write(
        plugin,
        ".mcp.json",
        '{"mcpServers":{"evidence-lane":{"command":"python","args":[]}}}\n',
    )
    _write(plugin, "COPYRIGHT.md", "# Copyright\n")
    _write(plugin, "LICENSE.md", "# Proprietary license\n")
    _write(plugin, "README.md", "# Evidence Lane plugin\n")
    _write(plugin, "THIRD_PARTY_NOTICES.md", "# Third-party notices\n")
    _write(
        plugin,
        ".codex-plugin/plugin.json",
        json.dumps(
            {
                "name": "evidence-lane-plugin",
                "version": VERSION,
                "mcpServers": "./.mcp.json",
            }
        )
        + "\n",
    )
    _write(plugin, "assets/evidence-lane-icon.png", b"png")
    _write(
        plugin,
        "chatgpt-app-connection.json",
        json.dumps(
            {
                "host": "CHATGPT",
                "delivery": "REGISTERED_REMOTE_MCP_ONLY",
                "codex_install_manifest_reference": False,
                "google_drive_bundled": False,
                "direct_stdio_fallback_allowed": False,
            }
        )
        + "\n",
    )
    _write(plugin, "chatgpt-app-submission.json", "{}\n")
    _write(
        plugin,
        "release-channels.json",
        json.dumps(
            {
                "schema": "evidence-lane.release-channels.v1",
                "stable": {
                    "release": "1.5.0",
                    "native_server_identity": "evidence-lane",
                    "native_tool_count": 62,
                    "native_read_tool_count": 21,
                    "native_write_tool_count": 41,
                    "skill_count": 15,
                    "codex_apps_allowed": False,
                    "google_drive_bundled": False,
                },
                "future_test": {
                    "enabled": False,
                    "may_replace_stable_before_acceptance": False,
                },
                "promotion_gate": {
                    "explicit_six_way_hil_required": True,
                    "fail_closed_on_version_mismatch": True,
                },
                "history": {"append_only": True, "hash_chained": True},
            }
        )
        + "\n",
    )
    _write(
        plugin,
        "scripts/codex-release-channel.json",
        json.dumps(
            {
                "schema": "evidence-lane.codex-release-channel.v2",
                "stable": {
                    "release": "2.0.0",
                    "native_server_identity": "evidence-lane",
                    "native_tool_count": 62,
                    "native_read_tool_count": 21,
                    "native_write_tool_count": 41,
                    "skill_count": 15,
                    "codex_apps_allowed": False,
                    "generated_namespace_allowed": False,
                    "direct_stdio_fallback_allowed": False,
                    "google_drive_bundled": False,
                },
                "future_test": {
                    "enabled": False,
                    "may_replace_stable_before_acceptance": False,
                },
        "archive": {"release": "1.5.0"},
        "remote_git_policy": {
            "effective_release": "2.0.0",
            "per_push_confirmation_token_required": False,
            "automatic_push_scope": (
                "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
            ),
            "host_managed_credentials_only": True,
            "main_push_allowed": False,
            "merge_allowed": False,
            "pull_request_acceptance_allowed": False,
            "force_push_allowed": False,
        },
        "host_split": {
                    "chatgpt_connection_artifacts_packaged_with_codex": False,
                    "remote_website_artifacts_packaged_with_codex": False,
                },
                "host_storage_tunnel_matrix": {
                    "routing_axes_independent": True,
                    "account_tier_affects_routing": False,
                    "api_billing_affects_routing": False,
                    "headless_api": {
                        "local_or_persistent_pv_storage": (
                            "LOCAL_SQLITE_WHEN_DURABLE"
                        ),
                        "ephemeral_pv_storage": (
                            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
                        ),
                        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
                        "flash_frequency": "EVERY_INVOCATION_ENTRY",
                    },
                    "interactive_codex_app_local_or_persistent": {
                        "pv_storage": "DURABLE_LOCAL_SQLITE",
                        "tunnel_setup_frequency": (
                            "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"
                        ),
                        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
                    },
                    "interactive_codex_app_ephemeral_vm": {
                        "pv_storage": (
                            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
                        ),
                        "tunnel_setup_frequency": (
                            "ONCE_PER_EPHEMERAL_VM_INSTANCE"
                        ),
                        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
                        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
                    },
                },
                "promotion_gate": {
                    "explicit_six_way_hil_required": True,
                    "fail_closed_on_version_mismatch": True,
                },
            }
        )
        + "\n",
    )
    _write(
        plugin,
        "scripts/codex_release/install_codex_stable.py",
        "# fixture installer\n",
    )
    _write(
        plugin,
        "scripts/codex_release/Restart-EvidenceLaneCodex.ps1",
        "# fixture restart helper\n",
    )
    _write(
        plugin,
        "scripts/codex_release/accept_codex_stable.py",
        "# fixture acceptance checker\n",
    )
    _write(plugin, "evidence/prompt_studio/manifest.json", "{}\n")
    _write(plugin, "evidence/prompt_studio/studio_search.sqlite", b"sqlite")
    _write(plugin, "remote_adapter/app/manifest.ts", "export const manifest = {};\n")
    _write(plugin, "remote_adapter/package.json", '{"dependencies":{}}\n')
    _write(plugin, "remote_adapter/pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(plugin, "pyproject.toml", '[project]\nname="fixture"\nversion="2.0.0"\n')
    _write(plugin, "requirements.lock.txt", "mcp==1.28.1\n")
    _write(plugin, "src/evidence_lane_plugin/__init__.py", "VERSION = 'fixture'\n")
    for index in range(15):
        _write(
            plugin,
            f"skills/skill-{index:02d}/SKILL.md",
            f"---\nname: skill-{index:02d}\n---\nFixture.\n",
        )
    for index in range(18):
        lane = f"remote_adapter/public/dummy-lane-packages/lane-{index:02d}"
        _write(plugin, f"{lane}/lane-{index:02d}.dot", "digraph fixture {}\n")
        _write(plugin, f"{lane}/lane-{index:02d}.mmd", "flowchart LR\n")
        _write(plugin, f"{lane}/lane-{index:02d}.mmd.8k.png", b"png")
        _write(plugin, f"{lane}/lane-{index:02d}.mmd.vector.svg", "<svg/>\n")
        _write(plugin, f"{lane}/lane-{index:02d}_sector_v001.sqlite", b"sqlite")
        _write(plugin, f"{lane}/refresh_receipt.json", "{}\n")
    return plugin


def test_rehearsal_rejects_combined_native_and_registered_app_manifest(
    tmp_path: Path,
) -> None:
    plugin = _plugin_fixture(tmp_path)
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["apps"] = "./.app.json"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    _write(plugin, ".app.json", '{"apps":{"evidence-lane":{}}}\n')

    with pytest.raises(PackageBoundaryError, match="must remain separate"):
        _build(plugin, tmp_path / "combined-manifest")


def _build(plugin: Path, output: Path) -> dict[str, object]:
    return build_rehearsal(
        plugin_root=plugin,
        output_dir=output,
        base_commit=COMMIT,
        base_tree=TREE,
        expected_version=VERSION,
    )


def test_rehearsal_is_deterministic_posix_safe_and_non_lifecycle(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(plugin, ".venv/secret.txt", "sk-this-is-excluded-and-never-scanned-123456")
    _write(plugin, "remote_adapter/node_modules/cache.js", "ignored\n")
    _write(plugin, "remote_adapter/leaked.js.map", "{}\n")
    _write(plugin, "remote_adapter/tsconfig.tsbuildinfo", "{}\n")
    _write(plugin, "src/evidence_lane_plugin/__pycache__/cache.pyc", b"ignored")
    _write(plugin, "src/evidence_lane_plugin.egg-info/SOURCES.txt", "ignored\n")
    _write(
        plugin,
        ".codex-plugin/migrated-command-skills/source-command-evi-plan/SKILL.md",
        "Codex-generated compatibility adapter.\n",
    )
    _write(
        plugin,
        "_evidence_lane_rehearsal/exit-slip.json",
        '{"status":"STALE_PACKAGED_METADATA_MUST_BE_REPLACED"}\n',
    )
    _write(plugin, ".env", "OPENAI_API_KEY=sk-this-is-excluded-1234567890\n")

    first = _build(plugin, tmp_path / "first")
    second = _build(plugin, tmp_path / "second")
    assert first["archive"]["sha256"] == second["archive"]["sha256"]  # type: ignore[index]
    assert first["working_source_manifest_sha256"] == second[
        "working_source_manifest_sha256"
    ]
    assert first["boundary"] == BOUNDARY
    assert first["governed_candidate_created"] is False
    assert first["git_invoked"] is False
    assert first["accepted_pointer_moved"] is False
    assert first["skill_count"] == 15
    assert first["canonical_lane_count"] == 18
    assert Path(str(first["receipt_path"])).name.startswith("LOCAL_PACKAGE_REHEARSAL_")

    first_archive = Path(str(first["receipt_path"])).parent / first["archive"][  # type: ignore[index]
        "filename"
    ]
    second_archive = Path(str(second["receipt_path"])).parent / second["archive"][  # type: ignore[index]
        "filename"
    ]
    assert first_archive.read_bytes() == second_archive.read_bytes()
    with zipfile.ZipFile(first_archive) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert names == sorted(names)
        assert all("\\" not in name and ".." not in Path(name).parts for name in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
        assert all(((info.external_attr >> 16) & 0o777) == 0o600 for info in infos)
        assert not any(".venv" in name for name in names)
        assert not any("node_modules" in name for name in names)
        assert not any("__pycache__" in name for name in names)
        assert not any(".egg-info" in name for name in names)
        assert not any("migrated-command-skills" in name for name in names)
        assert names.count("_evidence_lane_rehearsal/exit-slip.json") == 1
        assert not any(name.endswith(".map") for name in names)
        assert not any(name.endswith(".tsbuildinfo") for name in names)
        assert ".env" not in names
        for required in ("README.md", "LICENSE.md", "COPYRIGHT.md", "THIRD_PARTY_NOTICES.md"):
            assert required in names
        assert "scripts/codex-release-channel.json" in names
        assert "scripts/codex_release/install_codex_stable.py" in names
        assert "scripts/codex_release/Restart-EvidenceLaneCodex.ps1" in names
        assert "scripts/codex_release/accept_codex_stable.py" in names
        assert "chatgpt-app-connection.json" not in names
        assert "chatgpt-app-submission.json" not in names
        assert "release-channels.json" not in names
        assert not any(name.startswith("evidence/") for name in names)
        assert not any(name.startswith("remote_adapter/") for name in names)
        exit_slip = json.loads(
            archive.read("_evidence_lane_rehearsal/exit-slip.json")
        )
        assert exit_slip["status"] == (
            "LOCAL_REHEARSAL_VERIFIED_NOT_A_GOVERNED_CANDIDATE"
        )


@pytest.mark.parametrize("suffix", [".glb", ".gltf"])
def test_rehearsal_rejects_generated_3d_assets(tmp_path: Path, suffix: str) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(plugin, f"assets/generated-model{suffix}", b"3d")
    with pytest.raises(PackageBoundaryError, match="Generated 3D assets"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_rejects_high_confidence_secret_material(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(plugin, "config.txt", "sk-proj-this-is-not-a-real-key-but-must-be-rejected-123456\n")
    with pytest.raises(PackageBoundaryError, match="openai_key"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_rejects_meshy_dependency_or_mcp_binding(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(
        plugin,
        "pyproject.toml",
        '[project]\nname="fixture"\nversion="2.0.0"\ndependencies=["meshy-sdk==1.0.0"]\n',
    )
    with pytest.raises(PackageBoundaryError, match="Meshy"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_fails_closed_on_skill_inventory_drift(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    (plugin / "skills" / "skill-14" / "SKILL.md").unlink()
    with pytest.raises(PackageBoundaryError, match="Expected 15 skills"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_fails_closed_without_package_legal_notices(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    (plugin / "THIRD_PARTY_NOTICES.md").unlink()
    with pytest.raises(PackageBoundaryError, match="Required package members"):
        _build(plugin, tmp_path / "output")
