from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "evidence-lane-plugin" / "scripts"


def _load(name: str, path: Path):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def _plugin_fixture(tmp_path: Path) -> Path:
    plugin = tmp_path / "cache" / "market" / "plugin" / "2.1.0"
    plugin.mkdir(parents=True)
    (plugin / "requirements.lock.txt").write_text(
        "mcp==1.28.1 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    (plugin / "requirements.toolchain.lock.txt").write_text(
        "graphviz==0.21 --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    (plugin / "requirements.torch-cpu.lock.txt").write_text(
        "torch==2.10.0 --hash=sha256:" + "c" * 64 + "\n",
        encoding="utf-8",
    )
    (plugin / "requirements.torch-nvidia.lock.txt").write_text(
        "torch==2.10.0 --hash=sha256:" + "d" * 64 + "\n",
        encoding="utf-8",
    )
    (plugin / "requirements.onnx-directml.lock.txt").write_text(
        "onnxruntime-directml==1.24.4 --hash=sha256:" + "e" * 64 + "\n",
        encoding="utf-8",
    )
    return plugin


def test_runtime_projection_survives_reconstructed_plugin_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _load(
        "evidence_lane_runtime_contract_test", SCRIPTS / "runtime_contract.py"
    )
    plugin = _plugin_fixture(tmp_path)
    durable = tmp_path / "durable"
    monkeypatch.setenv("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", str(durable))

    marker = contract.runtime_marker(plugin)
    environment = contract.runtime_environment(plugin)
    environment.mkdir(parents=True)
    contract.write_marker(plugin, marker)

    local_environment = plugin / ".venv"
    local_environment.mkdir()
    local_environment.rmdir()

    assert environment.is_relative_to(durable)
    assert not environment.is_relative_to(plugin)
    assert contract.marker_is_valid(plugin, marker) is True


def test_runtime_marker_fails_closed_when_dependency_lock_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _load(
        "evidence_lane_runtime_contract_drift_test", SCRIPTS / "runtime_contract.py"
    )
    plugin = _plugin_fixture(tmp_path)
    monkeypatch.setenv("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", str(tmp_path / "durable"))
    marker = contract.runtime_marker(plugin)
    contract.write_marker(plugin, marker)

    (plugin / "requirements.lock.txt").write_text(
        "mcp==1.28.2 --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )

    assert contract.marker_is_valid(plugin, marker) is False
    assert contract.runtime_marker(plugin) != marker


def test_runtime_marker_is_hash_sealed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _load(
        "evidence_lane_runtime_contract_seal_test", SCRIPTS / "runtime_contract.py"
    )
    plugin = _plugin_fixture(tmp_path)
    monkeypatch.setenv("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", str(tmp_path / "durable"))
    marker = contract.runtime_marker(plugin)
    contract.write_marker(plugin, marker)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["status"] = "FAILED"
    marker.write_text(json.dumps(payload), encoding="utf-8")

    assert contract.marker_is_valid(plugin, marker) is False


def test_empty_configured_data_root_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _load(
        "evidence_lane_runtime_contract_root_test", SCRIPTS / "runtime_contract.py"
    )
    plugin = _plugin_fixture(tmp_path)
    monkeypatch.setenv("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", "   ")

    with pytest.raises(RuntimeError, match="cannot be empty"):
        contract.runtime_environment(plugin)
