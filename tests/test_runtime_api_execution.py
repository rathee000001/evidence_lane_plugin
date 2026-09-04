from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.runtime_api import (
    RuntimeApiSettings,
    create_runtime_api,
    load_maintainer_dotenv,
    load_runtime_api_settings,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _runtime_settings(tmp_path: Path) -> RuntimeApiSettings:
    return RuntimeApiSettings(
        plugin_root=PLUGIN,
        hidden_state_root=(
            tmp_path / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
        ),
        host_profile="CODEX_DESKTOP",
    )


def test_runtime_api_rejects_non_hidden_state_root(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    with pytest.raises(ValueError, match="HIDDEN_RUNTIME_ROOT_REQUIRED"):
        create_runtime_api(
            RuntimeApiSettings(
                plugin_root=PLUGIN,
                hidden_state_root=tmp_path / "workspace-runtime",
                host_profile="CODEX_DESKTOP",
            )
        )


def test_runtime_api_health_and_multipart_stage_are_hidden(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    pytest.importorskip("aiofiles")
    from fastapi.testclient import TestClient

    settings = _runtime_settings(tmp_path)
    client = TestClient(create_runtime_api(settings))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "PASS",
        "plane": "CODEX",
        "host_profile": "CODEX_DESKTOP",
        "loopback_only": True,
        "workspace_runtime": False,
    }
    staged = client.post(
        "/source-intake/stage",
        files={"file": ("source.txt", b"exact bytes", "text/plain")},
    )
    assert staged.status_code == 200
    receipt = staged.json()
    assert receipt["status"] == "STAGED_NOT_INGESTED"
    assert receipt["hidden_runtime_staging"] is True
    assert receipt["workspace_written"] is False
    target = settings.hidden_state_root / "source-intake-staging" / "source.txt"
    assert target.read_bytes() == b"exact bytes"


def test_pydantic_settings_and_dotenv_boundaries(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("pydantic_settings")
    hidden = tmp_path / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"
    monkeypatch.setenv("EVIDENCE_LANE_PLUGIN_ROOT", str(PLUGIN))
    monkeypatch.setenv("EVIDENCE_LANE_HIDDEN_STATE_ROOT", str(hidden))
    monkeypatch.setenv("EVIDENCE_LANE_HOST_PROFILE", "CODEX_CLI")
    settings = load_runtime_api_settings()
    assert settings.host_profile == "CODEX_CLI"
    assert settings.hidden_state_root == hidden

    dotenv = tmp_path / ".env.maintainer"
    dotenv.write_text("TOKEN=not-returned\nMODE=local\n", encoding="utf-8")
    receipt = load_maintainer_dotenv(dotenv)
    assert receipt["production_runtime_used"] is False
    assert receipt["values_returned"] is False
    assert receipt["key_names"] == ["MODE", "TOKEN"]
    assert "not-returned" not in str(receipt)


def test_runtime_api_declares_actual_server_dependencies() -> None:
    source = (PLUGIN / "src" / "evidence_lane_plugin" / "runtime_api.py").read_text(
        encoding="utf-8"
    )
    assert "from fastapi import FastAPI" in source
    assert "import uvicorn" in source
    assert "from pydantic_settings import BaseSettings" in source
    assert "import aiofiles" in source
    assert "orjson.dumps" in source
    assert "File(...)" in source


def test_tunnel_prewarm_and_runtime_bind_exact_hidden_root_and_host() -> None:
    runner = (PLUGIN / "scripts" / "run_mcp.py").read_text(encoding="utf-8")
    installer = (
        PLUGIN / "scripts" / "windows_tunnel" / "Install-EvidenceLaneTunnel.ps1"
    ).read_text(encoding="utf-8")
    assert 'parser.add_argument("--runtime-control-root")' in runner
    assert 'startup_mode.add_argument("--bootstrap-only"' in runner
    assert '"schema": "evidence-lane.codex-runtime-bootstrap.v1"' in runner
    assert '"toolchain_inspected": False' in runner
    assert "_activate_installed_runtime_authority" in runner
    assert '"runtime_authority": runtime_authority' in runner
    assert '"--host-profile"' in runner
    assert 'os.environ["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"]' in runner
    assert 'os.environ["EVIDENCE_LANE_HOST_PROFILE"]' in runner
    assert runner.count('"-B",') == 5
    assert "--runtime-control-root $ExactRuntimeControlRoot" in installer
    assert "--host-profile CODEX_DESKTOP" in installer
    assert "EVIDENCE_LANE_HOST_PROFILE = 'CODEX_DESKTOP'" in installer
