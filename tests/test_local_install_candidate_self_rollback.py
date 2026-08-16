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
    spec = importlib.util.spec_from_file_location("row203_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(stable: str, fallback: str, candidate: str, *, selected: str) -> str:
    lines: list[str] = []
    for selector in [stable, fallback, candidate]:
        enabled = selector == selected
        lines.extend(
            [
                f'[plugins."{selector}"]',
                f"enabled = {str(enabled).lower()}",
                f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                f"enabled = {str(enabled).lower()}",
            ]
        )
    return "\n".join([*lines, ""])


def _write_sealed(module, path: Path, value: dict[str, object]) -> str:
    sealed = dict(value)
    sealed["receipt_sha256"] = module.hashlib.sha256(
        module._json_bytes(sealed)
    ).hexdigest().upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(module._json_bytes(sealed))
    return module._sha256(path)


def _fixture(module, tmp_path: Path) -> dict[str, object]:
    stable = "evidence-lane-plugin@evidence-lane-github"
    fallback = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    candidate = "evidence-lane-plugin@evidence-lane-v220-testing-new"
    transaction_id = "local_test_tx_" + "b" * 40
    data_root = tmp_path / "pv"
    codex_home = tmp_path / "codex"
    config_path = codex_home / "config.toml"
    config_path.parent.mkdir(parents=True)
    prior = _config(stable, fallback, candidate, selected=stable)
    failed = _config(stable, fallback, candidate, selected=candidate)
    config_path.write_text(failed, encoding="utf-8", newline="")
    authority_root = data_root / "installations" / "codex-v200"
    backup = authority_root / "config-archives" / "prior.toml"
    backup.parent.mkdir(parents=True)
    backup.write_text(prior, encoding="utf-8", newline="")
    commit_path = (
        authority_root
        / "local-test-transactions"
        / f"COMMIT_{transaction_id}.json"
    )
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
                "after_sha256": module._sha256(config_path),
                "supported_codex_api": "config/batchWrite",
            },
            "rollback_capable": True,
            "rollback_config_backup": str(backup),
            "rollback_config_backup_sha256": module._sha256(backup),
            "readiness": {"ready": False},
            "runtime_ready_before_task_reopen": False,
            "candidate_created_or_accepted": False,
            "pointer_moved": False,
            "hil_inferred": False,
        },
    )
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    return {
        "stable": stable,
        "fallback": fallback,
        "candidate": candidate,
        "transaction_id": transaction_id,
        "data_root": data_root,
        "codex_home": codex_home,
        "config_path": config_path,
        "backup": backup,
        "commit_path": commit_path,
        "commit_sha256": commit_sha256,
        "executable": executable,
    }


def _fake_restore(authority: dict[str, object], codex_home: Path) -> dict[str, object]:
    config_path = codex_home / "config.toml"
    backup = Path(str(authority["rollback_config_backup"]))
    config_path.write_bytes(backup.read_bytes())
    return {
        "status": "PASS",
        "candidate_disabled": True,
        "restored_selector": authority["last_known_good_selector"],
        "exact_prior_config_restored": True,
        "prior_config_sha256": authority["rollback_config_backup_sha256"],
    }


def test_candidate_self_rollback_is_terminal_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    restore_calls = 0

    def restore(**kwargs: object):
        nonlocal restore_calls
        restore_calls += 1
        return _fake_restore(
            dict(kwargs["authority"]), Path(kwargs["codex_home"])
        )

    monkeypatch.setattr(module, "_restore_exact_prior_local_test_config", restore)
    monkeypatch.setattr(
        module,
        "_probe_recovered_selector_health",
        lambda **kwargs: {
            "status": "PASS",
            "selected_selector": kwargs["selected_selector"],
            "candidate_disabled": True,
            "native_catalog": dict(module.EXPECTED_CATALOG),
        },
    )
    args = {
        "commit_receipt_path": fixture["commit_path"],
        "commit_receipt_sha256": fixture["commit_sha256"],
        "executable": fixture["executable"],
        "codex_home": fixture["codex_home"],
        "data_root": fixture["data_root"],
        "allow_byte_frozen_fallback": False,
    }
    result = module._recover_local_test_candidate_failure(**args)

    assert result["status"] == "PASS"
    assert result["state"] == "RECOVERED_EXACT_LAST_KNOWN_GOOD"
    assert result["attempt_count"] == 1
    assert result["candidate_disabled"] is True
    assert result["exact_prior_config_restored"] is True
    assert result["restart_loop_started"] is False
    assert result["restart_or_reload_requests"] == 0
    assert result["one_terminal_receipt"] is True
    assert result["replayed"] is False
    assert restore_calls == 1
    assert Path(result["receipt_path"]).is_file()

    monkeypatch.setattr(
        module,
        "_restore_exact_prior_local_test_config",
        lambda **_kwargs: pytest.fail("terminal replay performed another rollback"),
    )
    replay = module._recover_local_test_candidate_failure(**args)
    assert replay["replayed"] is True
    assert replay["receipt_file_sha256"] == result["receipt_file_sha256"]
    assert restore_calls == 1


def test_candidate_self_rollback_uses_sealed_fallback_only_as_second_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    fallback_authority_sha256 = "F" * 64

    monkeypatch.setattr(
        module,
        "_restore_exact_prior_local_test_config",
        lambda **kwargs: _fake_restore(
            dict(kwargs["authority"]), Path(kwargs["codex_home"])
        ),
    )

    def health(**kwargs: object):
        if kwargs["selected_selector"] == fixture["stable"]:
            raise module.InstallationError("stable probe failed")
        return {
            "status": "PASS",
            "selected_selector": kwargs["selected_selector"],
            "candidate_disabled": True,
            "native_catalog": dict(module.EXPECTED_CATALOG),
        }

    monkeypatch.setattr(module, "_probe_recovered_selector_health", health)
    monkeypatch.setattr(
        module,
        "_load_byte_frozen_fallback_recovery_authority",
        lambda **_kwargs: {
            "fallback_selector": fixture["fallback"],
            "fallback_version": "2.1.0+codex.fixture",
            "fallback_authority": {
                "fallback_authority_sha256": fallback_authority_sha256,
            },
        },
    )
    monkeypatch.setattr(
        module,
        "_activate_byte_frozen_recovery_fallback",
        lambda **_kwargs: {
            "status": "PASS",
            "candidate_disabled": True,
            "fallback_enabled": True,
            "fallback_authority_sha256": fallback_authority_sha256,
        },
    )
    result = module._recover_local_test_candidate_failure(
        commit_receipt_path=fixture["commit_path"],
        commit_receipt_sha256=fixture["commit_sha256"],
        executable=fixture["executable"],
        codex_home=fixture["codex_home"],
        data_root=fixture["data_root"],
        allow_byte_frozen_fallback=True,
    )

    assert result["status"] == "PASS"
    assert result["state"] == "RECOVERED_BYTE_FROZEN_FALLBACK"
    assert result["attempt_count"] == module.LOCAL_TEST_RECOVERY_MAX_ATTEMPTS
    assert [row["target_kind"] for row in result["attempts"]] == [
        "EXACT_LAST_KNOWN_GOOD",
        "BYTE_FROZEN_FALLBACK",
    ]
    assert len({row["correlation_id"] for row in result["attempts"]}) == 2
    assert result["all_attempts_have_unique_correlation_ids"] is True
    assert result["fallback_authority_sha256"] == fallback_authority_sha256
    assert result["candidate_disabled"] is True


def test_failed_health_without_fallback_writes_one_fail_closed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    monkeypatch.setattr(
        module,
        "_restore_exact_prior_local_test_config",
        lambda **kwargs: _fake_restore(
            dict(kwargs["authority"]), Path(kwargs["codex_home"])
        ),
    )
    monkeypatch.setattr(
        module,
        "_probe_recovered_selector_health",
        lambda **_kwargs: (_ for _ in ()).throw(
            module.InstallationError("health failed")
        ),
    )
    result = module._recover_local_test_candidate_failure(
        commit_receipt_path=fixture["commit_path"],
        commit_receipt_sha256=fixture["commit_sha256"],
        executable=fixture["executable"],
        codex_home=fixture["codex_home"],
        data_root=fixture["data_root"],
        allow_byte_frozen_fallback=False,
    )

    assert result["status"] == "FAIL_CLOSED"
    assert result["state"] == "TERMINAL_RECOVERY_EXHAUSTED_FAIL_CLOSED"
    assert result["attempt_count"] == 1
    assert result["candidate_disabled"] is True
    assert result["exact_prior_config_restored"] is True
    assert result["byte_frozen_fallback_used"] is False
    assert result["one_terminal_receipt"] is True
    assert result["restart_loop_started"] is False


def test_missing_fallback_law_never_runs_a_second_activation_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    monkeypatch.setattr(
        module,
        "_restore_exact_prior_local_test_config",
        lambda **kwargs: _fake_restore(
            dict(kwargs["authority"]), Path(kwargs["codex_home"])
        ),
    )
    monkeypatch.setattr(
        module,
        "_probe_recovered_selector_health",
        lambda **_kwargs: (_ for _ in ()).throw(
            module.InstallationError("stable health failed")
        ),
    )
    monkeypatch.setattr(
        module,
        "_activate_byte_frozen_recovery_fallback",
        lambda **_kwargs: pytest.fail("fallback ran without sealed authority"),
    )
    result = module._recover_local_test_candidate_failure(
        commit_receipt_path=fixture["commit_path"],
        commit_receipt_sha256=fixture["commit_sha256"],
        executable=fixture["executable"],
        codex_home=fixture["codex_home"],
        data_root=fixture["data_root"],
        allow_byte_frozen_fallback=True,
    )

    assert result["status"] == "FAIL_CLOSED"
    assert result["attempt_count"] == 1
    assert result["candidate_disabled"] is True
    assert result["fallback_authority_sha256"] is None
    assert result["fallback_authority_error"]["error_class"] == (
        "InstallationError"
    )


def test_exact_prior_config_restore_uses_one_supported_atomic_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    fixture = _fixture(module, tmp_path)
    config_path = Path(fixture["config_path"])
    backup = Path(fixture["backup"])
    authority = module._load_local_test_recovery_authority(
        commit_receipt_path=fixture["commit_path"],
        commit_receipt_sha256=fixture["commit_sha256"],
        data_root=fixture["data_root"],
    )

    def render_plugins(plugins: dict[str, object]) -> str:
        lines: list[str] = []
        for selector, raw_settings in plugins.items():
            settings = dict(raw_settings or {})
            server = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
            assert isinstance(server, dict)
            lines.extend(
                [
                    f'[plugins."{selector}"]',
                    f'enabled = {str(bool(settings.get("enabled"))).lower()}',
                    f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                    f'enabled = {str(bool(server.get("enabled"))).lower()}',
                ]
            )
        return "\n".join([*lines, ""])

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
            self.methods: list[str] = []
            self.config_version = "sha256:" + "a" * 64

        def write(self, value: str) -> int:
            request = json.loads(value)
            method = str(request.get("method"))
            self.methods.append(method)
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
                params = dict(request["params"])
                assert params["expectedVersion"] == self.config_version
                edit = dict(params["edits"][0])
                assert edit["keyPath"] == "plugins"
                assert edit["mergeStrategy"] == "replace"
                config_path.write_text(
                    render_plugins(dict(edit["value"])),
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
                json.dumps({"id": request.get("id"), "result": result}) + "\n"
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
    result = module._restore_exact_prior_local_test_config(
        authority=authority,
        executable=fixture["executable"],
        codex_home=fixture["codex_home"],
    )

    assert result["candidate_disabled"] is True
    assert result["exact_prior_config_restored"] is True
    assert result["config"]["supported_codex_api"] == "config/batchWrite"
    assert result["config"]["plugin_table_write_count"] == 1
    assert config_path.read_bytes() == backup.read_bytes()
    assert module._sha256(config_path) == module._sha256(backup)
    assert fake.methods.count("config/batchWrite") == 1
    assert fake.methods.count("config/read") == 1
