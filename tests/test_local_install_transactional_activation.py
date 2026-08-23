from __future__ import annotations

import importlib.util
import json
import queue
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
    spec = importlib.util.spec_from_file_location("row202_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(stable: str, fallback: str, candidate: str) -> str:
    return "\n".join(
        [
            f'[plugins."{stable}"]',
            "enabled = true",
            f'[plugins."{stable}".mcp_servers."evidence-lane"]',
            "enabled = true",
            f'[plugins."{fallback}"]',
            "enabled = false",
            f'[plugins."{fallback}".mcp_servers."evidence-lane"]',
            "enabled = false",
            f'[plugins."{candidate}"]',
            "enabled = false",
            f'[plugins."{candidate}".mcp_servers."evidence-lane"]',
            "enabled = false",
            "",
        ]
    )


def _all_disabled_config(stable: str, fallback: str, candidate: str) -> str:
    return _config(stable, fallback, candidate).replace("enabled = true", "enabled = false")


def _all_disabled_plugin_only_config(
    stable: str,
    fallback: str,
    candidate: str,
) -> str:
    return "\n".join(
        [
            f'[plugins."{selector}"]\nenabled = false'
            for selector in (stable, fallback, candidate)
        ]
        + [""]
    )


def _stale_local_mcp_config(
    stable: str,
    fallback: str,
    candidate: str,
) -> str:
    return "\n".join(
        [
            f'[plugins."{stable}"]',
            "enabled = false",
            f'[plugins."{stable}".mcp_servers."evidence-lane"]',
            "enabled = false",
            f'[plugins."{fallback}"]',
            "enabled = false",
            f'[plugins."{fallback}".mcp_servers."evidence-lane"]',
            "enabled = false",
            f'[plugins."{candidate}"]',
            "enabled = false",
            f'[plugins."{candidate}".mcp_servers."evidence-lane"]',
            "enabled = true",
            "",
        ]
    )


def _post_add_transient_config(
    stable: str,
    fallback: str,
    candidate: str,
) -> str:
    return _all_disabled_config(stable, fallback, candidate).replace(
        f'[plugins."{candidate}"]\nenabled = false',
        f'[plugins."{candidate}"]\nenabled = true',
    )


def _active_local_config(stable: str, fallback: str, candidate: str) -> str:
    return _all_disabled_config(stable, fallback, candidate).replace(
        f'[plugins."{candidate}"]\nenabled = false',
        f'[plugins."{candidate}"]\nenabled = true',
    ).replace(
        f'[plugins."{candidate}".mcp_servers."evidence-lane"]\nenabled = false',
        f'[plugins."{candidate}".mcp_servers."evidence-lane"]\nenabled = true',
    )


def _write_sealed(module, path: Path, value: dict[str, object]) -> str:
    sealed = dict(value)
    sealed["receipt_sha256"] = module.hashlib.sha256(
        module._json_bytes(sealed)
    ).hexdigest().upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(module._json_bytes(sealed))
    return module._sha256(path)


def test_readiness_never_infers_host_ready_from_install_or_prewarm() -> None:
    module = _module()
    pending = module._local_test_runtime_readiness(
        installed=True,
        restart_or_reload_completed=False,
        hooks_trusted=False,
        exact_identity_verified=True,
        exact_catalog_verified=True,
        prompt_capture_verified=False,
        smoke_probes_passed=False,
        active=False,
    )
    assert pending["state"] == "INSTALLED_RESTART_OR_RELOAD_REQUIRED"
    assert pending["ready"] is False
    assert pending["runtime_ready_before_task_reopen"] is False
    assert pending["readiness_inferred_from_install_or_prewarm"] is False

    ready = module._local_test_runtime_readiness(
        installed=True,
        restart_or_reload_completed=True,
        hooks_trusted=True,
        exact_identity_verified=True,
        exact_catalog_verified=True,
        prompt_capture_verified=True,
        smoke_probes_passed=True,
        active=True,
    )
    assert ready["state"] == "READY"
    assert ready["ready"] is True


def test_disabled_host_normalization_may_omit_only_the_disabled_mcp_state() -> None:
    module = _module()
    selector = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    assert module._evidence_plugin_activation_states(
        {selector: {"enabled": False}}
    ) == {selector: False}

    with pytest.raises(
        module.InstallationError,
        match="no matching native MCP state",
    ):
        module._evidence_plugin_activation_states(
            {selector: {"enabled": True}}
        )

    with pytest.raises(
        module.InstallationError,
        match="activation pair is inconsistent",
    ):
        module._evidence_plugin_activation_states(
            {
                selector: {
                    "enabled": True,
                    "mcp_servers": {
                        "evidence-lane": {"enabled": False}
                    },
                }
            }
        )

    normalized = module._normalize_disabled_local_mcp_boundary(
        {
            selector: {
                "enabled": False,
                "mcp_servers": {
                    "evidence-lane": {"enabled": True}
                },
            }
        },
        plugin_selector=selector,
    )
    assert normalized[selector]["enabled"] is False
    assert normalized[selector]["mcp_servers"]["evidence-lane"][
        "enabled"
    ] is False

    post_add = module._normalize_local_post_add_boundary(
        {selector: {"enabled": True}},
        plugin_selector=selector,
    )
    assert post_add[selector]["enabled"] is False
    assert post_add[selector]["mcp_servers"]["evidence-lane"][
        "enabled"
    ] is False

    stable = "evidence-lane-plugin@evidence-lane-github"
    with pytest.raises(
        module.InstallationError,
        match="non-local live MCP",
    ):
        module._normalize_disabled_local_mcp_boundary(
            {
                stable: {
                    "enabled": False,
                    "mcp_servers": {
                        "evidence-lane": {"enabled": True}
                    },
                },
                selector: {
                    "enabled": False,
                    "mcp_servers": {
                        "evidence-lane": {"enabled": True}
                    },
                },
            },
            plugin_selector=selector,
        )


def test_prepare_keeps_one_last_known_good_and_seals_exact_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    codex_home = tmp_path / "codex"
    data_root = tmp_path / "pv"
    marketplace_root = tmp_path / "marketplace"
    marketplace_root.mkdir()
    (codex_home / "config.toml").parent.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        _config(stable, fallback, candidate),
        encoding="utf-8",
        newline="",
    )
    calls: list[list[str]] = []

    def fake_run(_exe: Path, _home: Path, args: list[str]):
        calls.append(args)
        if args == ["plugin", "list", "--json"]:
            return {
                "installed": [
                    {"pluginId": stable, "version": "2.1", "enabled": True},
                    {"pluginId": fallback, "version": "2.1", "enabled": False},
                    {"pluginId": candidate, "version": "3.0", "enabled": False},
                ]
            }
        if args == ["plugin", "marketplace", "list", "--json"]:
            return {"marketplaces": []}
        raise AssertionError(args)

    monkeypatch.setattr(module, "_run_codex", fake_run)
    receipt = module._prepare_local_test_reinstall(
        executable=tmp_path / "codex.exe",
        codex_home=codex_home,
        data_root=data_root,
        plugin_selector=candidate,
        marketplace_name="evidence-lane-v300-testing-new",
        marketplace_root=marketplace_root,
    )

    backup = Path(receipt["rollback_config_backup"])
    assert receipt["transaction_mode"] == "COMPARE_AND_SWAP"
    assert receipt["last_known_good_selector"] == stable
    assert receipt["candidate_must_remain_disabled_until_commit"] is True
    assert receipt["switch_count"] == 0
    assert backup.read_bytes() == (codex_home / "config.toml").read_bytes()
    assert module._sha256(backup) == receipt["rollback_config_backup_sha256"]
    assert ["plugin", "remove", candidate, "--json"] not in calls
    assert receipt["same_selector_update_via_plugin_add"] is True
    assert receipt["known_failed_plugin_remove_route_invoked"] is False


def test_prepare_rejects_already_enabled_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    monkeypatch.setattr(
        module,
        "_run_codex",
        lambda *_args, **_kwargs: {
            "installed": [
                {"pluginId": candidate, "version": "3.0", "enabled": True}
            ]
        },
    )
    with pytest.raises(module.InstallationError, match="last-known-good"):
        module._prepare_local_test_reinstall(
            executable=tmp_path / "codex.exe",
            codex_home=tmp_path / "codex",
            data_root=tmp_path / "pv",
            plugin_selector=candidate,
            marketplace_name="evidence-lane-v300-testing-new",
            marketplace_root=tmp_path / "marketplace",
        )


@pytest.mark.parametrize(
    "recovery_boundary",
    ["stale_mcp", "post_add", "active_aligned"],
)
def test_prepare_disabled_hook_recovery_requires_sealed_commit_and_all_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_boundary: str,
) -> None:
    module = _module()
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    codex_home = tmp_path / "codex"
    data_root = tmp_path / "pv"
    marketplace_root = tmp_path / "marketplace"
    marketplace_root.mkdir()
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        {
            "stale_mcp": _stale_local_mcp_config(stable, fallback, candidate),
            "post_add": _post_add_transient_config(stable, fallback, candidate),
            "active_aligned": _active_local_config(stable, fallback, candidate),
        }[recovery_boundary],
        encoding="utf-8",
        newline="",
    )
    authority_root = data_root / "installations" / "codex-v200"
    prior_backup = authority_root / "config-archives" / "prior.toml"
    prior_backup.parent.mkdir(parents=True)
    prior_backup.write_text(
        _config(stable, fallback, candidate),
        encoding="utf-8",
        newline="",
    )
    transaction_id = "local_test_tx_" + "d" * 40
    commit_path = authority_root / "local-test-transactions" / f"COMMIT_{transaction_id}.json"
    commit_sha256 = _write_sealed(
        module,
        commit_path,
        {
            "schema": module.LOCAL_TEST_COMMIT_SCHEMA,
            "status": "PASS",
            "state": "CANDIDATE_SWITCHED_ONCE_RESTART_OR_RELOAD_REQUIRED",
            "transaction_id": transaction_id,
            "candidate_selector": candidate,
            "last_known_good_selector": stable,
            "compare_and_swap": True,
            "switch_count": 1,
            "candidate_enabled": True,
            "last_known_good_enabled": False,
            "config": {
                "after_sha256": "A" * 64,
                "supported_codex_api": "config/batchWrite",
            },
            "rollback_capable": True,
            "rollback_config_backup": str(prior_backup),
            "rollback_config_backup_sha256": module._sha256(prior_backup),
            "readiness": {"ready": False},
            "runtime_ready_before_task_reopen": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )
    calls: list[list[str]] = []

    def fake_run(_exe: Path, _home: Path, arguments: list[str]):
        calls.append(arguments)
        if arguments == ["plugin", "list", "--json"]:
            return {
                "installed": [
                    {"pluginId": stable, "version": "2.1", "enabled": False},
                    {"pluginId": fallback, "version": "2.1", "enabled": False},
                    {
                        "pluginId": candidate,
                        "version": "3.0",
                        "enabled": recovery_boundary in {"post_add", "active_aligned"},
                    },
                ]
            }
        if arguments == ["plugin", "marketplace", "list", "--json"]:
            return {"marketplaces": []}
        raise AssertionError(arguments)

    monkeypatch.setattr(module, "_run_codex", fake_run)

    def fake_normalize_write(
        *,
        executable: Path,
        codex_home: Path,
        expected_before_sha256: str,
        plugins: dict[str, object],
        exact_after_sha256: str | None = None,
    ) -> dict[str, object]:
        del executable, codex_home, exact_after_sha256
        assert expected_before_sha256 == module._sha256(config_path)
        lines: list[str] = []
        for selector, raw_settings in plugins.items():
            settings = dict(raw_settings or {})
            lines.extend(
                [
                    f'[plugins."{selector}"]',
                    f'enabled = {str(bool(settings.get("enabled"))).lower()}',
                ]
            )
            server = dict(settings.get("mcp_servers") or {}).get(
                "evidence-lane"
            )
            if isinstance(server, dict):
                lines.extend(
                    [
                        f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                        f'enabled = {str(bool(server.get("enabled"))).lower()}',
                    ]
                )
        config_path.write_text(
            "\n".join([*lines, ""]),
            encoding="utf-8",
            newline="",
        )
        return {
            "status": "PASS",
            "supported_codex_api": "config/batchWrite",
            "before_sha256": expected_before_sha256,
            "after_sha256": module._sha256(config_path),
        }

    monkeypatch.setattr(
        module,
        "_batch_write_recovery_plugins",
        fake_normalize_write,
    )
    receipt = module._prepare_local_test_reinstall(
        executable=tmp_path / "codex.exe",
        codex_home=codex_home,
        data_root=data_root,
        plugin_selector=candidate,
        marketplace_name=module.LOCAL_TESTING_MARKETPLACE_NAME,
        marketplace_root=marketplace_root,
        disabled_hook_recovery_commit=commit_path,
        disabled_hook_recovery_commit_sha256=commit_sha256,
    )

    assert receipt["transaction_mode"] == "DISABLED_LOCAL_HOOK_RECOVERY"
    normalization = receipt["disabled_mcp_boundary_normalization"]
    assert normalization["status"] == "PASS"
    assert normalization["normalization_mode"] == {
        "stale_mcp": "STALE_LOCAL_MCP_AFTER_UI_DISABLE",
        "post_add": "LOCAL_PLUGIN_ADD_TRANSIENT",
        "active_aligned": "ACTIVE_LOCAL_SELECTOR_REINSTALL_BOUNDARY",
    }[recovery_boundary]
    assert normalization["plugin_enabled_before"] is (
        recovery_boundary in {"post_add", "active_aligned"}
    )
    assert normalization["mcp_enabled_before"] is (
        recovery_boundary in {"stale_mcp", "active_aligned"}
    )
    assert normalization["mcp_enabled_after"] is False
    assert normalization["supported_codex_api"] == "config/batchWrite"
    assert receipt["plugin_list_post_add_transient"] is (
        recovery_boundary in {"post_add", "active_aligned"}
    )
    assert receipt["all_evidence_lane_selectors_disabled_before_reinstall"] is True
    assert receipt["recovery_switch_target"] == candidate
    assert receipt["recovery_rollback_state"] == "ALL_EVIDENCE_LANE_SELECTORS_DISABLED"
    assert receipt["prior_commit_authority"]["file_sha256"] == commit_sha256
    assert receipt["transaction_id"].startswith("local_hook_recovery_")
    assert ["plugin", "remove", candidate, "--json"] not in calls
    assert receipt["same_selector_update_via_plugin_add"] is True
    assert receipt["known_failed_plugin_remove_route_invoked"] is False

    monkeypatch.setattr(
        module,
        "_run_codex",
        lambda *_args, **_kwargs: {
            "installed": [
                {"pluginId": stable, "version": "2.1", "enabled": True},
                {"pluginId": fallback, "version": "2.1", "enabled": False},
                {"pluginId": candidate, "version": "3.0", "enabled": False},
            ]
        },
    )
    with pytest.raises(
        module.InstallationError,
        match="either every selector disabled or the exact local post-add transient",
    ):
        module._prepare_local_test_reinstall(
            executable=tmp_path / "codex.exe",
            codex_home=codex_home,
            data_root=data_root,
            plugin_selector=candidate,
            marketplace_name=module.LOCAL_TESTING_MARKETPLACE_NAME,
            marketplace_root=marketplace_root,
            disabled_hook_recovery_commit=commit_path,
            disabled_hook_recovery_commit_sha256=commit_sha256,
        )


def test_installer_body_uses_activation_readiness_not_isolated_prewarm() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'else "STAGE_CANDIDATE_DISABLED"' in text
    assert '"RECOVER_DISABLED_LOCAL"' in text
    assert '"state": "CANDIDATE_STAGED_DISABLED"' in text
    assert '"switch_count": 0' in text
    assert '"rollback_capable": True' in text
    assert (
        'activation.get("runtime_ready_before_task_reopen") is True'
        in text
    )
    assert (
        'runtime_prewarm.get("runtime_ready_before_task_reopen") is True'
        not in text
    )


def test_commit_route_joins_sealed_host_proof_without_task_or_helper_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    transaction_id = "local_test_tx_" + "a" * 40
    data_root = tmp_path / "pv"
    codex_home = tmp_path / "codex"
    authority_root = data_root / "installations" / "codex-v200"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        _config(stable, fallback, candidate),
        encoding="utf-8",
        newline="",
    )
    baseline_sha256 = module._sha256(config_path)
    prepared_backup = authority_root / "config-archives" / "prepared.toml"
    prepared_backup.parent.mkdir(parents=True)
    prepared_backup.write_bytes(config_path.read_bytes())
    version = "3.0.0+codex.fixture"
    installed_path = (
        codex_home
        / "plugins"
        / "cache"
        / module.LOCAL_TESTING_MARKETPLACE_NAME
        / module.PLUGIN_NAME
        / version
    )
    installed_path.mkdir(parents=True)

    stage_path = authority_root / "INSTALL_STAGE.json"
    stage_sha256 = _write_sealed(
        module,
        stage_path,
        {
            "schema": module.INSTALL_SCHEMA,
            "status": "PASS",
            "plugin": {"version": version},
            "activation": {
                "state": "CANDIDATE_STAGED_DISABLED_RESTART_TRUST_REQUIRED",
                "plugin_add": {"installedPath": str(installed_path)},
                "transaction": {
                    "schema": module.LOCAL_TEST_STAGE_SCHEMA,
                    "transaction_id": transaction_id,
                    "state": "CANDIDATE_STAGED_DISABLED",
                    "last_known_good_selector": stable,
                    "last_known_good_config_sha256": baseline_sha256,
                    "candidate_selector": candidate,
                    "candidate_enabled": False,
                    "switch_count": 0,
                    "compare_and_swap": True,
                    "rollback_capable": True,
                    "rollback_config_backup": str(prepared_backup),
                    "rollback_config_backup_sha256": module._sha256(
                        prepared_backup
                    ),
                },
            },
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    observed: dict[str, object] = {}
    candidate_enabled = False

    def fake_transaction(**kwargs: object):
        nonlocal candidate_enabled
        observed.update(kwargs)
        mode = str(kwargs["activation_mode"])
        if mode == "COMMIT_CANDIDATE":
            candidate_enabled = True
        return (
            {
                "status": "PASS",
                "hook_count": len(module.EXPECTED_CODEX_HOST_HOOK_EVENTS),
                "activation_mode": mode,
            },
            {
                "compare_and_swap": mode == "COMMIT_CANDIDATE",
                "switch_count": 1 if mode == "COMMIT_CANDIDATE" else 0,
                "rollback_config_backup": str(prepared_backup),
            },
        )

    monkeypatch.setattr(module, "_trust_sealed_plugin_hooks", fake_transaction)
    monkeypatch.setattr(
        module,
        "_run_codex",
        lambda *_args, **_kwargs: {
            "installed": [
                {"pluginId": stable, "enabled": not candidate_enabled},
                {"pluginId": fallback, "enabled": False},
                {
                    "pluginId": candidate,
                    "version": version,
                    "enabled": candidate_enabled,
                    "installedPath": str(installed_path),
                },
            ]
        },
    )
    monkeypatch.setattr(
        module,
        "_prewarm_installed_runtime",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "tool_count": module.EXPECTED_CATALOG["tools"],
            "catalog_expected": dict(module.EXPECTED_CATALOG),
        },
    )
    proof = module._seal_local_test_host_proof(
        stage_receipt_path=stage_path,
        stage_receipt_sha256=stage_sha256,
        executable=executable,
        codex_home=codex_home,
        data_root=data_root,
        hook_cwd=tmp_path,
    )
    assert proof["readiness"]["state"] == "TRUSTED_INACTIVE"
    assert proof["runtime_ready_before_task_reopen"] is False
    assert observed["activation_mode"] == "VERIFY_STAGED_CANDIDATE"
    result = module._commit_local_test_transaction(
        stage_receipt_path=stage_path,
        stage_receipt_sha256=stage_sha256,
        host_proof_path=Path(proof["receipt_path"]),
        host_proof_sha256=proof["receipt_file_sha256"],
        executable=executable,
        codex_home=codex_home,
        data_root=data_root,
        hook_cwd=tmp_path,
    )

    assert observed["activation_mode"] == "COMMIT_CANDIDATE"
    assert observed["expected_commit_config_sha256"] == baseline_sha256
    assert result["switch_count"] == 1
    assert result["runtime_ready_before_task_reopen"] is False
    assert result["readiness"]["state"] == "INSTALLED_RESTART_OR_RELOAD_REQUIRED"
    assert result["task_binding_used_for_authorization"] is False
    assert result["goal_recovery_invoked"] is False
    assert result["installer_helper_is_separate"] is True
    assert result["tunnel_invoked"] is False
    committed = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert committed["receipt_sha256"] == result["receipt_sha256"]


def test_stage_reads_disabled_candidate_without_hooks_list_or_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    codex_home = tmp_path / "codex"
    data_root = tmp_path / "pv"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    original = _config(stable, fallback, candidate)
    config_path.write_text(original, encoding="utf-8", newline="")
    baseline_sha256 = module._sha256(config_path)
    prepared_backup = (
        data_root
        / "installations"
        / "codex-v200"
        / "config-archives"
        / "prepared.toml"
    )
    prepared_backup.parent.mkdir(parents=True)
    prepared_backup.write_bytes(config_path.read_bytes())
    marketplace_file = (
        codex_home
        / "local-marketplaces"
        / "evidence-lane-v300-testing-new"
        / ".agents"
        / "plugins"
        / "marketplace.json"
    )
    marketplace_file.parent.mkdir(parents=True)
    marketplace_file.write_text("{}\n", encoding="utf-8", newline="")
    events = sorted(module.EXPECTED_CODEX_HOST_HOOK_EVENTS)

    class Stream:
        def __init__(self) -> None:
            self.lines: queue.Queue[str | None] = queue.Queue()

        def __iter__(self):
            return self

        def __next__(self) -> str:
            value = self.lines.get(timeout=5)
            if value is None:
                raise StopIteration
            return value

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = Stream()
            self.stderr = Stream()
            self.stdin = self
            self.returncode: int | None = None
            self.writes: list[dict[str, object]] = []

        def write(self, value: str) -> int:
            request = json.loads(value)
            self.writes.append(request)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialized":
                return len(value)
            if method == "initialize":
                result: dict[str, object] = {}
            elif method == "config/read":
                result = {
                    "config": {},
                    "origins": {},
                    "layers": [
                        {
                            "name": {
                                "type": "user",
                                "file": str(config_path.resolve()),
                                "profile": None,
                            },
                            "version": "sha256:" + "a" * 64,
                            "disabledReason": None,
                        }
                    ],
                }
            elif method == "config/batchWrite":
                assert request["params"]["expectedVersion"] == "sha256:" + "a" * 64
                result = {"status": "ok", "version": "sha256:" + "b" * 64}
            elif method == "plugin/read":
                params = dict(request["params"])
                assert Path(params["marketplacePath"]) == marketplace_file
                result = {
                    "plugin": {
                        "marketplaceName": "evidence-lane-v300-testing-new",
                        "summary": {
                            "id": candidate,
                            "installed": True,
                            "enabled": False,
                        },
                        "hooks": [
                            {
                                "eventName": event,
                                "key": f"{candidate}:hooks/hooks.json:{event}:0:0",
                            }
                            for event in events
                        ],
                    }
                }
            elif method == "hooks/list":
                raise AssertionError("disabled candidate must not use hooks/list")
            else:
                raise AssertionError(request)
            self.stdout.lines.put(
                json.dumps({"id": request_id, "result": result}) + "\n"
            )
            return len(value)

        def flush(self) -> None:
            return None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0
            self.stdout.lines.put(None)
            self.stderr.lines.put(None)

        def kill(self) -> None:
            self.terminate()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.returncode = 0
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: fake)
    hook_receipt, config_receipt = module._trust_sealed_plugin_hooks(
        executable=tmp_path / "codex.exe",
        codex_home=codex_home,
        data_root=data_root,
        hook_cwd=tmp_path,
        plugin_selector=candidate,
        defer_hook_trust_to_user=True,
        activation_mode="STAGE_CANDIDATE_DISABLED",
        last_known_good_selector=stable,
        last_known_good_config_sha256=baseline_sha256,
        rollback_config_backup=str(prepared_backup),
    )

    methods = [str(row["method"]) for row in fake.writes]
    assert methods == [
        "initialize",
        "initialized",
        "config/read",
        "config/batchWrite",
        "plugin/read",
    ]
    assert hook_receipt["status"] == "USER_TRUST_PENDING"
    assert hook_receipt["hook_count"] == len(
        module.EXPECTED_CODEX_HOST_HOOK_EVENTS
    ) == 11
    assert hook_receipt["candidate_enabled"] is False
    assert {row["trust_status"] for row in hook_receipt["records"]} == {
        "pending_native_ui_review"
    }
    assert config_receipt["switch_count"] == 0
    assert config_receipt["candidate_enabled_after_write"] is False
    parsed = module.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["plugins"][stable]["enabled"] is True
    assert parsed["plugins"][candidate]["enabled"] is False


@pytest.mark.parametrize("post_switch_trusted", [True, False])
def test_commit_mode_switches_once_or_atomically_restores_exact_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    post_switch_trusted: bool,
) -> None:
    module = _module()
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    codex_home = tmp_path / "codex"
    data_root = tmp_path / "pv"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    events = sorted(module.EXPECTED_CODEX_HOST_HOOK_EVENTS)
    trust_toml = "".join(
        [
            f'[hooks.state."{candidate}:hooks/hooks.json:{event}:0:0"]\n'
            f'trusted_hash = "sha256:{index + 1:064x}"\n'
            for index, event in enumerate(events)
        ]
    )
    original = _config(stable, fallback, candidate) + trust_toml
    config_path.write_text(original, encoding="utf-8", newline="")
    baseline_sha256 = module._sha256(config_path)
    prepared_backup = (
        data_root
        / "installations"
        / "codex-v200"
        / "config-archives"
        / "prepared.toml"
    )
    prepared_backup.parent.mkdir(parents=True)
    prepared_backup.write_bytes(config_path.read_bytes())
    marketplace_file = (
        codex_home
        / "local-marketplaces"
        / "evidence-lane-v300-testing-new"
        / ".agents"
        / "plugins"
        / "marketplace.json"
    )
    marketplace_file.parent.mkdir(parents=True)
    marketplace_file.write_text("{}\n", encoding="utf-8", newline="")

    def render_plugins(plugins: dict[str, object]) -> str:
        lines: list[str] = []
        for selector, raw_settings in plugins.items():
            settings = dict(raw_settings or {})
            mcp = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
            assert isinstance(mcp, dict)
            lines.extend(
                [
                    f'[plugins."{selector}"]',
                    f'enabled = {str(bool(settings.get("enabled"))).lower()}',
                    f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                    f'enabled = {str(bool(mcp.get("enabled"))).lower()}',
                ]
            )
        return "\n".join([*lines, ""]) + trust_toml

    class Stream:
        def __init__(self) -> None:
            self.lines: queue.Queue[str | None] = queue.Queue()

        def __iter__(self):
            return self

        def __next__(self) -> str:
            value = self.lines.get(timeout=5)
            if value is None:
                raise StopIteration
            return value

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = Stream()
            self.stderr = Stream()
            self.stdin = self
            self.returncode: int | None = None
            self.writes: list[dict[str, object]] = []
            self.hook_reads = 0
            self.config_version = "sha256:" + "a" * 64

        def write(self, value: str) -> int:
            request = json.loads(value)
            self.writes.append(request)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialized":
                return len(value)
            if method == "initialize":
                result: dict[str, object] = {}
            elif method == "config/read":
                result = {
                    "config": {},
                    "origins": {},
                    "layers": [
                        {
                            "name": {
                                "type": "user",
                                "file": str(config_path.resolve()),
                                "profile": None,
                            },
                            "version": self.config_version,
                            "disabledReason": None,
                        }
                    ],
                }
            elif method == "hooks/list":
                self.hook_reads += 1
                trusted = post_switch_trusted
                hooks = [
                    {
                        "eventName": event,
                        "key": f"{candidate}:hooks/hooks.json:{event}:0:0",
                        "pluginId": candidate,
                        "source": "plugin",
                        "isManaged": False,
                        "enabled": True,
                        "currentHash": "sha256:" + f"{index + 1:064x}",
                        "trustStatus": "trusted" if trusted else "untrusted",
                    }
                    for index, event in enumerate(events)
                ]
                result = {
                    "data": [
                        {
                            "cwd": str(tmp_path.resolve()),
                            "hooks": hooks,
                            "warnings": [],
                            "errors": [],
                        }
                    ]
                }
            elif method == "plugin/read":
                result = {
                    "plugin": {
                        "marketplaceName": "evidence-lane-v300-testing-new",
                        "summary": {
                            "id": candidate,
                            "installed": True,
                            "enabled": False,
                        },
                        "hooks": [
                            {
                                "eventName": event,
                                "key": f"{candidate}:hooks/hooks.json:{event}:0:0",
                            }
                            for event in events
                        ],
                    }
                }
            elif method == "config/batchWrite":
                params = dict(request["params"])
                expected = str(params["expectedVersion"])
                assert expected == self.config_version
                edit = dict(params["edits"][0])
                assert edit["keyPath"] == "plugins"
                assert edit["mergeStrategy"] == "replace"
                plugins = dict(edit["value"])
                config_path.write_text(
                    render_plugins(plugins),
                    encoding="utf-8",
                    newline="",
                )
                self.config_version = "sha256:" + module._sha256(config_path).lower()
                result = {
                    "status": "ok",
                    "version": self.config_version,
                }
            else:
                raise AssertionError(request)
            self.stdout.lines.put(
                json.dumps({"id": request_id, "result": result}) + "\n"
            )
            return len(value)

        def flush(self) -> None:
            return None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0
            self.stdout.lines.put(None)
            self.stderr.lines.put(None)

        def kill(self) -> None:
            self.terminate()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.returncode = 0
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: fake)
    kwargs = {
        "executable": tmp_path / "codex.exe",
        "codex_home": codex_home,
        "data_root": data_root,
        "hook_cwd": tmp_path,
        "plugin_selector": candidate,
        "activation_mode": "COMMIT_CANDIDATE",
        "last_known_good_selector": stable,
        "last_known_good_config_sha256": baseline_sha256,
        "rollback_config_backup": str(prepared_backup),
        "expected_commit_config_sha256": baseline_sha256,
    }
    if post_switch_trusted:
        hook_receipt, config_receipt = module._trust_sealed_plugin_hooks(**kwargs)
        parsed = module.tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert hook_receipt["status"] == "PASS"
        assert hook_receipt["hook_trust_config_mutated"] is False
        assert config_receipt["switch_count"] == 1
        assert parsed["plugins"][candidate]["enabled"] is True
        assert parsed["plugins"][stable]["enabled"] is False
        assert [row["method"] for row in fake.writes].count(
            "config/batchWrite"
        ) == 1
    else:
        with pytest.raises(module.InstallationError, match="restored.*atomically"):
            module._trust_sealed_plugin_hooks(**kwargs)
        assert config_path.read_text(encoding="utf-8") == original
        assert module._sha256(config_path) == baseline_sha256
        assert [row["method"] for row in fake.writes].count(
            "config/batchWrite"
        ) == 2


@pytest.mark.parametrize("plugin_add_transient", [False, True])
@pytest.mark.parametrize("trust_verifies", [True, False])
@pytest.mark.parametrize("keep_disabled", [False, True])
def test_disabled_local_hook_recovery_switches_only_local_or_restores_all_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trust_verifies: bool,
    plugin_add_transient: bool,
    keep_disabled: bool,
) -> None:
    module = _module()
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    codex_home = tmp_path / "codex"
    data_root = tmp_path / "pv"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    all_disabled = _all_disabled_config(stable, fallback, candidate)
    config_path.write_text(
        (
            _post_add_transient_config(stable, fallback, candidate)
            if plugin_add_transient
            else all_disabled
        ),
        encoding="utf-8",
        newline="",
    )
    baseline_sha256 = module._sha256(config_path)
    backup = (
        data_root
        / "installations"
        / "codex-v200"
        / "config-archives"
        / "all-disabled.toml"
    )
    backup.parent.mkdir(parents=True)
    backup.write_text(all_disabled, encoding="utf-8", newline="")
    rollback_sha256 = module._sha256(backup)
    events = sorted(module.EXPECTED_CODEX_HOST_HOOK_EVENTS)
    plugin_table = module.tomllib.loads(config_path.read_text(encoding="utf-8"))[
        "plugins"
    ]
    hook_state: dict[str, object] = {}

    def render() -> None:
        lines: list[str] = []
        for selector, raw_settings in plugin_table.items():
            settings = dict(raw_settings or {})
            mcp = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
            assert isinstance(mcp, dict)
            lines.extend(
                [
                    f'[plugins."{selector}"]',
                    f'enabled = {str(bool(settings.get("enabled"))).lower()}',
                    f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                    f'enabled = {str(bool(mcp.get("enabled"))).lower()}',
                ]
            )
        for key, raw_value in hook_state.items():
            value = dict(raw_value or {})
            trusted_hash = str(value.get("trusted_hash") or "")
            enabled = bool(value.get("enabled", True))
            lines.extend(
                [
                    f'[hooks.state."{key}"]',
                    f'trusted_hash = "{trusted_hash}"',
                    f'enabled = {str(enabled).lower()}',
                ]
            )
        config_path.write_text(
            "\n".join([*lines, ""]),
            encoding="utf-8",
            newline="",
        )

    class Stream:
        def __init__(self) -> None:
            self.lines: queue.Queue[str | None] = queue.Queue()

        def __iter__(self):
            return self

        def __next__(self) -> str:
            value = self.lines.get(timeout=5)
            if value is None:
                raise StopIteration
            return value

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = Stream()
            self.stderr = Stream()
            self.stdin = self
            self.returncode: int | None = None
            self.writes: list[dict[str, object]] = []
            self.hook_reads = 0
            self.config_version = "sha256:" + "1" * 64

        def write(self, value: str) -> int:
            nonlocal plugin_table, hook_state
            request = json.loads(value)
            self.writes.append(request)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialized":
                return len(value)
            if method == "initialize":
                result: dict[str, object] = {}
            elif method == "config/read":
                result = {
                    "config": {},
                    "origins": {},
                    "layers": [
                        {
                            "name": {
                                "type": "user",
                                "file": str(config_path.resolve()),
                                "profile": None,
                            },
                            "version": self.config_version,
                            "disabledReason": None,
                        }
                    ],
                }
            elif method == "config/batchWrite":
                assert request["params"]["expectedVersion"] == self.config_version
                for raw_edit in request["params"]["edits"]:
                    edit = dict(raw_edit)
                    if edit["keyPath"] == "plugins":
                        plugin_table = dict(edit["value"])
                    elif edit["keyPath"] == "hooks.state":
                        hook_state.update(dict(edit["value"]))
                    elif edit["keyPath"] == "hooks":
                        hook_state = dict(dict(edit["value"]).get("state") or {})
                    else:
                        raise AssertionError(edit)
                render()
                self.config_version = "sha256:" + module._sha256(config_path).lower()
                result = {"status": "ok", "version": self.config_version}
            elif method == "hooks/list":
                self.hook_reads += 1
                trusted = bool(hook_state) and (
                    trust_verifies or self.hook_reads == 1
                )
                hooks = []
                for index, event in enumerate(events):
                    key = f"{candidate}:hooks/hooks.json:{event}:0:0"
                    state = dict(hook_state.get(key) or {})
                    hooks.append(
                        {
                            "eventName": event,
                            "key": key,
                            "pluginId": candidate,
                            "source": "plugin",
                            "isManaged": False,
                            "enabled": bool(state.get("enabled", True)),
                            "currentHash": "sha256:" + f"{index + 1:064x}",
                            "trustStatus": "trusted" if trusted else "untrusted",
                        }
                    )
                result = {
                    "data": [
                        {
                            "cwd": str(tmp_path.resolve()),
                            "hooks": hooks,
                            "warnings": [],
                            "errors": [],
                        }
                    ]
                }
            else:
                raise AssertionError(request)
            self.stdout.lines.put(json.dumps({"id": request_id, "result": result}) + "\n")
            return len(value)

        def flush(self) -> None:
            return None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0
            self.stdout.lines.put(None)
            self.stderr.lines.put(None)

        def kill(self) -> None:
            self.terminate()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.returncode = 0
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: fake)
    kwargs = {
        "executable": tmp_path / "codex.exe",
        "codex_home": codex_home,
        "data_root": data_root,
        "hook_cwd": tmp_path,
        "plugin_selector": candidate,
        "activation_mode": "RECOVER_DISABLED_LOCAL",
        "last_known_good_selector": stable,
        "last_known_good_config_sha256": rollback_sha256,
        "rollback_config_backup": str(backup),
        "expected_commit_config_sha256": baseline_sha256,
        "keep_hooks_disabled_after_trust": keep_disabled,
    }
    if trust_verifies:
        hook_receipt, config_receipt = module._trust_sealed_plugin_hooks(**kwargs)
        parsed = module.tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert hook_receipt["status"] == "PASS"
        assert hook_receipt["disabled_local_recovery"] is True
        assert hook_receipt["plugin_add_transient_detected"] is (
            plugin_add_transient
        )
        assert config_receipt["plugin_add_transient_detected"] is (
            plugin_add_transient
        )
        assert config_receipt["switch_count"] == 1
        assert parsed["plugins"][candidate]["enabled"] is True
        assert parsed["plugins"][stable]["enabled"] is False
        assert parsed["plugins"][fallback]["enabled"] is False
        assert len(parsed["hooks"]["state"]) == len(
            module.EXPECTED_CODEX_HOST_HOOK_EVENTS
        ) == 11
        assert {
            row["enabled"] for row in hook_receipt["records"]
        } == {not keep_disabled}
        assert hook_receipt["after_enabled_states"] == [not keep_disabled]
        assert all(
            state["enabled"] is (not keep_disabled)
            for state in parsed["hooks"]["state"].values()
        )
        assert [row["method"] for row in fake.writes].count("config/batchWrite") == 2
    else:
        with pytest.raises(module.InstallationError, match="restored.*atomically"):
            module._trust_sealed_plugin_hooks(**kwargs)
        parsed = module.tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert all(
            settings["enabled"] is False
            for settings in parsed["plugins"].values()
        )
        assert dict(parsed.get("hooks") or {}) == {}
        assert [row["method"] for row in fake.writes].count("config/batchWrite") == 3


def test_config_write_refreshes_semantic_version_and_retries_one_conflict(
    tmp_path: Path,
) -> None:
    module = _module()
    config_path = tmp_path / "config.toml"
    config_path.write_text("model = \"gpt-5.6-sol\"\n", encoding="utf-8", newline="")
    raw_sha256 = module._sha256(config_path)
    replies: dict[int, dict[str, object]] = {}
    requests: list[dict[str, object]] = []
    semantic_versions = iter(["sha256:" + "1" * 64, "sha256:" + "2" * 64])
    writes = 0

    def send(request: dict[str, object]) -> None:
        nonlocal writes
        requests.append(request)
        request_id = int(request["id"])
        if request["method"] == "config/read":
            replies[request_id] = {
                "id": request_id,
                "result": {
                    "config": {},
                    "origins": {},
                    "layers": [
                        {
                            "name": {
                                "type": "user",
                                "file": str(config_path.resolve()),
                                "profile": None,
                            },
                            "version": next(semantic_versions),
                            "disabledReason": None,
                        }
                    ],
                },
            }
            return
        assert request["method"] == "config/batchWrite"
        writes += 1
        if writes == 1:
            replies[request_id] = {
                "id": request_id,
                "error": {
                    "code": -32600,
                    "message": "Configuration was modified since last read.",
                    "data": {"config_write_error_code": "configVersionConflict"},
                },
            }
        else:
            replies[request_id] = {
                "id": request_id,
                "result": {"status": "ok", "version": "sha256:" + "3" * 64},
            }

    def wait_for(
        request_id: int,
        *,
        timeout: float = 20.0,
        allow_error: bool = False,
    ) -> dict[str, object]:
        del timeout
        reply = replies[request_id]
        if "error" in reply and not allow_error:
            raise AssertionError("unexpected unhandled error")
        return reply

    receipt = module._batch_write_with_one_config_refresh(
        send=send,
        wait_for=wait_for,
        config_path=config_path,
        expected_raw_sha256=raw_sha256,
        edits=[{"keyPath": "plugins", "value": {}, "mergeStrategy": "replace"}],
        request_ids=((10, 11), (12, 13)),
    )

    assert receipt["config_read_count"] == 2
    assert receipt["config_write_attempt_count"] == 2
    assert receipt["config_conflict_retry_count"] == 1
    batch_requests = [row for row in requests if row["method"] == "config/batchWrite"]
    assert [row["params"]["expectedVersion"] for row in batch_requests] == [
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
    ]
