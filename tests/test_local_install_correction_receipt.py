from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "install_codex_stable.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("row204_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_sealed(module, path: Path, value: dict[str, object]) -> str:
    sealed = dict(value)
    sealed["receipt_sha256"] = module.hashlib.sha256(
        module._json_bytes(sealed)
    ).hexdigest().upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(module._json_bytes(sealed))
    return module._sha256(path)


def _config(stable: str, candidate: str) -> str:
    return "\n".join(
        [
            f'[plugins."{stable}"]',
            "enabled = true",
            f'[plugins."{stable}".mcp_servers."evidence-lane"]',
            "enabled = true",
            f'[plugins."{candidate}"]',
            "enabled = false",
            f'[plugins."{candidate}".mcp_servers."evidence-lane"]',
            "enabled = false",
            "",
        ]
    )


def _fixture(module, tmp_path: Path) -> dict[str, object]:
    stable = "evidence-lane-plugin@evidence-lane-github"
    candidate = "evidence-lane-plugin@evidence-lane-v220-testing-new"
    version = "2.1.0+codex.fixture"
    data_root = tmp_path / "pv"
    codex_home = tmp_path / "codex"
    installation_root = data_root / "installations" / "codex-v200"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        _config(stable, candidate), encoding="utf-8", newline=""
    )
    final_config_sha256 = module._sha256(config_path)
    failed_config_sha256 = "A" * 64
    installed_path = tmp_path / "cache" / version
    manifest_path = installed_path / ".codex-plugin" / "plugin.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"name": module.PLUGIN_NAME, "version": version}),
        encoding="utf-8",
    )

    base_path = installation_root / "INSTALL_BASE.json"
    base_sha256 = _write_sealed(
        module,
        base_path,
        {
            "schema": module.INSTALL_SCHEMA,
            "status": "PASS",
            "plugin": {"plugin_id": module.PLUGIN_NAME, "version": version},
            "activation": {
                "state": "INSTALLED_RESTART_REQUIRED",
                "plugin_add": {
                    "pluginId": stable,
                    "version": version,
                    "installedPath": str(installed_path),
                },
            },
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )
    current_path = installation_root / "CURRENT_INSTALLATION.json"
    current_sha256 = _write_sealed(
        module,
        current_path,
        {
            "schema": module.INSTALL_SCHEMA,
            "status": "PASS",
            "plugin": {
                "plugin_id": module.PLUGIN_NAME,
                "version": "2.2.0+codex.failed",
            },
            "activation": {
                "state": "INSTALLED_RESTART_REQUIRED",
                "plugin_add": {
                    "pluginId": candidate,
                    "version": "2.2.0+codex.failed",
                },
            },
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )
    transaction_id = "local_test_tx_" + "c" * 40
    recovery_path = (
        installation_root
        / "local-test-transactions"
        / f"RECOVERY_{transaction_id}.json"
    )
    recovery_sha256 = _write_sealed(
        module,
        recovery_path,
        {
            "schema": module.LOCAL_TEST_RECOVERY_SCHEMA,
            "status": "PASS",
            "state": "RECOVERED_EXACT_LAST_KNOWN_GOOD",
            "transaction_id": transaction_id,
            "commit_receipt_sha256": "B" * 64,
            "candidate_selector": candidate,
            "last_known_good_selector": stable,
            "selected_selector": stable,
            "controller_idempotent": True,
            "max_attempts": 2,
            "attempt_count": 1,
            "attempts": [
                {
                    "ordinal": 1,
                    "correlation_id": "local_test_recovery_" + "d" * 40,
                    "target_kind": "EXACT_LAST_KNOWN_GOOD",
                    "target_selector": stable,
                    "status": "PASS",
                    "evidence": {
                        "candidate_disabled": True,
                        "restore": {
                            "status": "PASS",
                            "candidate_disabled": True,
                            "exact_prior_config_restored": True,
                            "config": {
                                "supported_codex_api": "config/batchWrite",
                                "before_sha256": failed_config_sha256,
                                "after_sha256": final_config_sha256,
                                "plugin_table_write_count": 1,
                            },
                        },
                        "health": {
                            "status": "PASS",
                            "selected_selector": stable,
                            "selected_version": version,
                            "candidate_disabled": True,
                            "native_catalog": dict(module.EXPECTED_CATALOG),
                        },
                    },
                }
            ],
            "candidate_disabled": True,
            "exact_prior_config_restored": True,
            "byte_frozen_fallback_used": False,
            "one_terminal_receipt": True,
            "restart_loop_started": False,
            "restart_or_reload_requests": 0,
            "task_reopened": False,
            "install_correction_generation_required": True,
            "current_installation_updated": False,
            "runtime_ready_before_correction_generation": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )

    task_id = "019ffc89-407e-7ae3-b45d-fca78391d656"
    task_binding = installation_root / "task-bindings" / f"{task_id}.json"
    task_binding.parent.mkdir(parents=True)
    task_binding.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.codex-task-binding.v1",
                "state": "EXACT_TASK_BINDING_PREPARED",
                "project_id": "test-codex-evidence-lane-plugin",
                "evidence_session_id": "session_fixture",
                "task_id": task_id,
                "plugin_version": "2.2.0+codex.failed",
                "task_uri_sha256": "E" * 64,
                "install_receipt_sha256": current_sha256,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    goal_id = "01a0036f-32fa-79b2-8846-9c716d4fe777"
    payload = {
        "state": "ACTIVE_GOAL_BOUND",
        "project_id": "test-codex-evidence-lane-plugin",
        "evidence_session_id": "session_fixture",
        "task_id": goal_id,
        "task_uri_sha256": "F" * 64,
    }
    goal_binding = (
        installation_root / "goal-recovery" / "bindings" / f"{goal_id}.json"
    )
    goal_binding.parent.mkdir(parents=True)
    goal_binding.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.codex-goal-recovery-binding.v1",
                "payload": payload,
                "payload_sha256": module._ordered_json_sha256(payload),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    return {
        "stable": stable,
        "candidate": candidate,
        "version": version,
        "data_root": data_root,
        "codex_home": codex_home,
        "config_path": config_path,
        "installed_path": installed_path,
        "manifest_path": manifest_path,
        "base_path": base_path,
        "base_sha256": base_sha256,
        "current_path": current_path,
        "current_before_sha256": current_sha256,
        "recovery_path": recovery_path,
        "recovery_sha256": recovery_sha256,
        "task_binding": task_binding,
        "goal_binding": goal_binding,
        "executable": executable,
    }


def _host_proof(module, fixture: dict[str, object]) -> dict[str, object]:
    hook_trust: dict[str, object] = {
        "schema": module.HOOK_TRUST_SCHEMA,
        "status": "PASS",
        "plugin_selector": fixture["stable"],
        "hook_count": 8,
        "registered_events": sorted(module.EXPECTED_CODEX_HOST_HOOK_EVENTS),
    }
    hook_trust["receipt_sha256"] = hashlib.sha256(
        module._json_bytes(hook_trust)
    ).hexdigest().upper()
    proof: dict[str, object] = {
        "schema": "evidence-lane.codex-install-correction-host-proof.v1",
        "status": "PASS",
        "config_sha256": module._sha256(fixture["config_path"]),
        "selected_selector": fixture["stable"],
        "candidate_selector": fixture["candidate"],
        "candidate_disabled": True,
        "sole_enabled_evidence_lane_selector": True,
        "plugin_identity": {
            "plugin_id": module.PLUGIN_NAME,
            "plugin_selector": fixture["stable"],
            "version": fixture["version"],
            "plugin_manifest_sha256": module._sha256(fixture["manifest_path"]),
        },
        "cache_identity": {
            "installed_path": str(Path(fixture["installed_path"]).resolve()),
            "installed_path_sha256": hashlib.sha256(
                str(Path(fixture["installed_path"]).resolve()).encode("utf-8")
            ).hexdigest().upper(),
            "generated_cache_written_directly": False,
        },
        "hook_trust": hook_trust,
        "native_runtime": {
            "receipt_sha256": "1" * 64,
            "runtime_identity": {"plugin_version": fixture["version"]},
            "tool_catalog_sha256": "2" * 64,
            "catalog": dict(module.EXPECTED_CATALOG),
            "task_reopened": False,
        },
        "fresh_app_server_hook_read": True,
        "host_restart_requested": False,
        "task_reopened": False,
    }
    proof["receipt_sha256"] = hashlib.sha256(
        module._json_bytes(proof)
    ).hexdigest().upper()
    return proof


def test_correction_generation_seals_full_proof_and_repoints_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    task_binding_before = module._sha256(fixture["task_binding"])
    goal_binding_before = module._sha256(fixture["goal_binding"])
    proof_calls = 0

    def probe(**_kwargs: object):
        nonlocal proof_calls
        proof_calls += 1
        return _host_proof(module, fixture)

    monkeypatch.setattr(module, "_probe_install_correction_host_state", probe)
    args = {
        "recovery_receipt_path": fixture["recovery_path"],
        "recovery_receipt_sha256": fixture["recovery_sha256"],
        "base_installation_path": fixture["base_path"],
        "base_installation_sha256": fixture["base_sha256"],
        "executable": fixture["executable"],
        "codex_home": fixture["codex_home"],
        "data_root": fixture["data_root"],
        "hook_cwd": tmp_path,
    }
    result = module._seal_install_correction_generation(**args)

    correction_path = Path(result["correction_installation_path"])
    current_path = Path(result["current_installation_path"])
    assert result["status"] == "PASS"
    assert result["current_installation_points_to_correction_generation"] is True
    assert correction_path.read_bytes() == current_path.read_bytes()
    assert module._sha256(correction_path) == module._sha256(current_path)
    corrected = json.loads(current_path.read_text(encoding="utf-8"))
    generation = corrected["correction_generation"]
    assert corrected["schema"] == module.INSTALL_SCHEMA
    assert corrected["activation"]["plugin_add"]["pluginId"] == fixture["stable"]
    assert generation["schema"] == module.INSTALL_CORRECTION_GENERATION_SCHEMA
    assert generation["config_lineage"]["before_sha256"] == "A" * 64
    assert generation["config_lineage"]["after_sha256"] == module._sha256(
        fixture["config_path"]
    )
    assert generation["hook_trust_receipt_sha256"] == corrected["activation"][
        "hook_trust"
    ]["receipt_sha256"]
    assert generation["task_bindings_before"]["record_count"] == 2
    assert generation["task_bindings_after"]["record_count"] == 2
    assert generation["task_binding_registry_bytes_mutated"] is False
    assert generation["goal_recovery_manager_must_refresh_exact_calling_task"] is True
    assert result["exact_task_refresh_required_before_task_reopen"] is True
    assert result["runtime_ready_before_task_reopen"] is False
    assert module._sha256(fixture["task_binding"]) == task_binding_before
    assert module._sha256(fixture["goal_binding"]) == goal_binding_before
    pointer = json.loads(Path(result["pointer_receipt_path"]).read_text(encoding="utf-8"))
    assert pointer["current_equals_correction_generation"] is True
    assert pointer["current_installation_before_sha256"] == fixture[
        "current_before_sha256"
    ]
    assert proof_calls == 1

    monkeypatch.setattr(
        module,
        "_probe_install_correction_host_state",
        lambda **_kwargs: pytest.fail("replay repeated the host proof"),
    )
    replay = module._seal_install_correction_generation(**args)
    assert replay["replayed"] is True
    assert replay["current_installation_file_sha256"] == result[
        "current_installation_file_sha256"
    ]
    assert proof_calls == 1


def test_failed_fresh_host_proof_leaves_current_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    current_before = Path(fixture["current_path"]).read_bytes()
    monkeypatch.setattr(
        module,
        "_probe_install_correction_host_state",
        lambda **_kwargs: (_ for _ in ()).throw(
            module.InstallationError("fresh hook trust unavailable")
        ),
    )
    with pytest.raises(module.InstallationError, match="hook trust unavailable"):
        module._seal_install_correction_generation(
            recovery_receipt_path=fixture["recovery_path"],
            recovery_receipt_sha256=fixture["recovery_sha256"],
            base_installation_path=fixture["base_path"],
            base_installation_sha256=fixture["base_sha256"],
            executable=fixture["executable"],
            codex_home=fixture["codex_home"],
            data_root=fixture["data_root"],
            hook_cwd=tmp_path,
        )

    assert Path(fixture["current_path"]).read_bytes() == current_before
    assert not (
        Path(fixture["data_root"])
        / "installations"
        / "codex-v200"
        / "correction-generations"
    ).exists()
