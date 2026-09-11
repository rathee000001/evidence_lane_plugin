"""Owned transport descendants and explicit credential-provider boundaries."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.bounded_io import BoundedProcessResult, run_owned_bounded_process
from evidence_lane_plugin.errors import LaneError


def test_owned_process_returns_bounded_output(tmp_path):
    result = run_owned_bounded_process([sys.executable, '-I', '-S', '-c', 'print("owned")'], cwd=tmp_path)
    assert result.returncode == 0 and result.stdout.strip() == b'owned'


def test_timeout_kills_descendant_before_it_can_write(tmp_path):
    child = tmp_path / 'child.py'
    marker = tmp_path / 'descendant-wrote'
    child.write_text('import pathlib,time\ntime.sleep(2)\npathlib.Path(' + repr(str(marker)) + ').write_text("bad")\n')
    parent = 'import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-I","-S",' + repr(str(child)) + '])\ntime.sleep(20)'
    with pytest.raises(LaneError, match='duration budget') as error:
        run_owned_bounded_process([sys.executable, '-I', '-S', '-c', parent], cwd=tmp_path, timeout_seconds=1)
    assert error.value.code == 'BOUNDED_PROCESS_TIMEOUT'
    time.sleep(2)
    assert not marker.exists()


def test_output_overflow_kills_owned_process(tmp_path):
    with pytest.raises(LaneError) as error:
        run_owned_bounded_process([sys.executable, '-I', '-S', '-c', 'print("x"*100000)'], cwd=tmp_path, max_stdout_bytes=100)
    assert error.value.code == 'BOUNDED_PROCESS_OUTPUT_EXCEEDED'


def test_host_gcm_selection_is_explicit_and_command_is_quoted(tmp_path, monkeypatch):
    import subprocess

    import evidence_lane_plugin.git_adapter as owner
    subprocess.run(['git', 'init', str(tmp_path)], capture_output=True, check=True)
    provider = {'provider': 'host_git_credential_manager', 'executable': str(tmp_path.parent / "trusted dir's tool.exe"),
        'sha256': 'a' * 64, 'credential_values_read': False, 'authentication_observed': False}
    monkeypatch.setattr(owner, 'git_credential_provider', lambda repository: provider)
    calls = []
    def capture(command, **kwargs):
        calls.append((command, kwargs))
        return BoundedProcessResult(0, b'', b'')
    monkeypatch.setattr(owner, 'run_owned_bounded_process', capture)
    owner.workflow_git(tmp_path, ['ls-remote', '--', 'https://example.invalid/repo.git'], https=True)
    assert [x for x in calls[-1][0] if x.startswith('credential.helper=')] == ['credential.helper=']
    owner.workflow_git(tmp_path, ['ls-remote', '--', 'https://example.invalid/repo.git'], https=True, credential_provider=provider)
    import shlex
    helper = [x for x in calls[-1][0] if x.startswith('credential.helper=')][-1]
    assert shlex.split(helper.removeprefix('credential.helper=!exec ')) == [Path(provider['executable']).as_posix()]
    assert calls[-1][1]['env']['GCM_INTERACTIVE'] == 'false'
    assert 'http.followRedirects=false' in calls[-1][0]
    assert 'http.sslVerify=true' in calls[-1][0]
    with pytest.raises(LaneError) as error:
        owner.workflow_git(tmp_path, ['ls-remote'], https=True, credential_provider={**provider, 'sha256': 'b' * 64})
    assert error.value.code == 'GIT_CREDENTIAL_PROVIDER_CHANGED'


@pytest.mark.skipif(os.name != 'nt', reason='Current qualification host provides Windows GCM.')
def test_existing_host_gcm_metadata_does_not_read_credentials(tmp_path):
    from evidence_lane_plugin.git_adapter import git_credential_provider
    value = git_credential_provider(tmp_path)
    assert Path(value['executable']).name == 'git-credential-manager.exe'
    assert len(value['sha256']) == 64
    assert not value['credential_values_read'] and not value['authentication_observed']
