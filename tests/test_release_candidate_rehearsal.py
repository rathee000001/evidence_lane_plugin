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

VERSION = "1.4.0+codex.20260808180919"
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
    _write(plugin, ".app.json", '{"apps":{}}\n')
    _write(plugin, ".mcp.json", '{"mcpServers":{}}\n')
    _write(plugin, "COPYRIGHT.md", "# Copyright\n")
    _write(plugin, "LICENSE.md", "# Proprietary license\n")
    _write(plugin, "README.md", "# Evidence Lane plugin\n")
    _write(plugin, "THIRD_PARTY_NOTICES.md", "# Third-party notices\n")
    _write(
        plugin,
        ".codex-plugin/plugin.json",
        json.dumps({"name": "evidence-lane-plugin", "version": VERSION}) + "\n",
    )
    _write(plugin, "assets/evidence-lane-icon.png", b"png")
    _write(plugin, "chatgpt-app-submission.json", "{}\n")
    _write(plugin, "evidence/prompt_studio/manifest.json", "{}\n")
    _write(plugin, "evidence/prompt_studio/studio_search.sqlite", b"sqlite")
    _write(plugin, "remote_adapter/app/manifest.ts", "export const manifest = {};\n")
    _write(plugin, "remote_adapter/package.json", '{"dependencies":{}}\n')
    _write(plugin, "remote_adapter/pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(plugin, "pyproject.toml", '[project]\nname="fixture"\nversion="1.4.0"\n')
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
    assert first["lane_count"] == 18
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
        assert not any(name.endswith(".map") for name in names)
        assert not any(name.endswith(".tsbuildinfo") for name in names)
        assert ".env" not in names
        for required in ("README.md", "LICENSE.md", "COPYRIGHT.md", "THIRD_PARTY_NOTICES.md"):
            assert required in names
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
    _write(plugin, "remote_adapter/package.json", '{"dependencies":{"meshy-sdk":"1.0.0"}}\n')
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
