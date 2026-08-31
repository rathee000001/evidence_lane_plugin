from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from evidence_lane_plugin.constants import ENGINE_VERSION

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
CURRENT_VERSION = "3.0.0"
CURRENT_INSTALLATION_AUTHORITY = "codex-v300"


def _project_version(path: Path) -> str:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def test_current_source_version_has_one_canonical_identity() -> None:
    plugin_manifest = json.loads(
        (PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    adapter_manifest = json.loads(
        (ROOT / "apps" / "evidence-lane-app" / "package.json").read_text(
            encoding="utf-8"
        )
    )

    assert _project_version(ROOT / "pyproject.toml") == CURRENT_VERSION
    assert _project_version(PLUGIN / "pyproject.toml") == CURRENT_VERSION
    assert ENGINE_VERSION == CURRENT_VERSION
    assert plugin_manifest["version"] == CURRENT_VERSION
    assert adapter_manifest["version"] == CURRENT_VERSION


def test_cachebuster_metadata_is_not_stored_in_git_source_manifest() -> None:
    manifest = (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
        encoding="utf-8"
    )
    assert "+codex." not in manifest
    assert re.search(r'"version"\s*:\s*"3\.0\.0"', manifest)


def test_active_installation_routes_use_current_v300_authority_root() -> None:
    active_paths = (
        PLUGIN / "hooks" / "invoke_hook.ps1",
        PLUGIN / "scripts" / "codex_release" / "Prepare-EvidenceLaneCodexRestart.ps1",
        PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py",
        PLUGIN / "scripts" / "codex_release" / "verify_isolated_hook_runtime.py",
        PLUGIN / "src" / "evidence_lane_plugin" / "codex_turn_control.py",
        PLUGIN / "src" / "evidence_lane_plugin" / "runtime_activation.py",
    )
    for path in active_paths:
        text = path.read_text(encoding="utf-8")
        assert "codex-v200" not in text, path
        assert "evidence-lane-v200" not in text, path
        assert CURRENT_INSTALLATION_AUTHORITY in text, path


def test_currentness_headers_do_not_claim_a_moving_date() -> None:
    dated_currentness = re.compile(
        r"(?:current[- ]route[- ]refresh|evidence[- ]lane[- ]current[- ]route[- ]refresh)"
        r"[^\n]*"
        r"\b20\d\d-\d\d-\d\d\b",
        re.IGNORECASE,
    )
    for relative in (
        ".env.example",
        "CONTRIBUTING.md",
        "Dockerfile",
        "pyproject.toml",
        "requirements.in",
    ):
        assert not dated_currentness.search((ROOT / relative).read_text(encoding="utf-8"))
