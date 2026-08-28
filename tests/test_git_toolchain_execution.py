from __future__ import annotations

from types import SimpleNamespace

import pytest
from evidence_lane_plugin.git_optional import probe_git_arm
from evidence_lane_plugin.github_toolchain import (
    GitHubInspectionRequest,
    inspect_github_repository,
)


def test_git_cli_and_gitpython_identity_parity(source_repository) -> None:
    pytest.importorskip("git")
    receipt = probe_git_arm(source_repository, requested_mode="REQUIRED")
    assert receipt["status"] == "PASS"
    assert receipt["history_index_enabled"] is True
    assert receipt["gitpython"]["status"] == "PASS"
    assert receipt["gitpython"]["parity"] is True
    assert receipt["gitpython"]["head_commit"] == receipt["head_commit"]
    assert receipt["gitpython"]["head_tree"] == receipt["head_tree"]


def test_pygithub_adapter_is_secret_reference_only(monkeypatch) -> None:
    github = pytest.importorskip("github")

    class _Client:
        def get_repo(self, name: str):
            assert name == "owner/repository"
            return SimpleNamespace(
                full_name=name,
                default_branch="main",
                private=False,
                archived=False,
                get_branches=lambda: [
                    SimpleNamespace(
                        name="main",
                        commit=SimpleNamespace(sha="a" * 40),
                        protected=True,
                    )
                ],
                get_workflows=lambda: [
                    SimpleNamespace(id=1, name="CI", path=".github/workflows/ci.yml", state="active")
                ],
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(github.Auth, "Token", lambda value: ("token", len(value)))
    monkeypatch.setattr(github, "Github", lambda **_kwargs: _Client())
    secret_value = "secret-value-never-returned"
    receipt = inspect_github_repository(
        GitHubInspectionRequest(
            repository="owner/repository",
            secret_reference="dpapi://github-app/installation-token",
            network_allowed=True,
            host_profile="CODEX_DESKTOP",
        ),
        resolve_secret=lambda _reference: secret_value,
    )
    assert receipt["status"] == "PASS"
    assert receipt["engine"] == "PyGithub"
    assert receipt["write_authorized"] is False
    assert receipt["secret_value_logged"] is False
    assert secret_value not in str(receipt)


def test_pygithub_network_gate_is_explicit() -> None:
    with pytest.raises(ValueError, match="EXPLICIT_NETWORK_GRANT_REQUIRED"):
        inspect_github_repository(
            GitHubInspectionRequest(
                repository="owner/repository",
                secret_reference="dpapi://github-app/installation-token",
                host_profile="CODEX_CLI",
            ),
            resolve_secret=lambda _reference: "unused",
        )
