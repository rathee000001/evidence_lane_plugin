from __future__ import annotations

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.git_optional import probe_git_arm
from evidence_lane_plugin.github_toolchain import (
    GitHubInspectionRequest,
    github_readiness,
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
    import requests

    def no_network(*args, **kwargs):
        raise AssertionError('Readiness must not contact the external service')
    monkeypatch.setattr(requests.Session, 'request', no_network)
    secret_value = "secret-value-never-returned"
    monkeypatch.setenv('EVI_GITHUB_TEST', secret_value)
    receipt = github_readiness(None, {'config_env_keys': ['EVI_GITHUB_TEST']})
    assert receipt['ready'] and not receipt['remote_authentication_verified']
    assert secret_value not in str(receipt)


def test_pygithub_network_gate_is_explicit() -> None:
    with pytest.raises(LaneError) as failure:
        inspect_github_repository(GitHubInspectionRequest(repository='owner/repository'))
    assert failure.value.code == 'DELTA_REQUIRED'
    with pytest.raises(ValueError):
        GitHubInspectionRequest(repository='owner/repository', network_allowed=True, host_profile='CODEX_CLI')
