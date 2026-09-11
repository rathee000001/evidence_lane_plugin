"""Repository CI selects exact committed changes and reports executed outcomes."""
import importlib.util
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("maintainer_ci", ROOT / "scripts/maintainer_ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_policy_has_explicit_current_tests_and_no_universal_or_retired_defaults():
    policy = ci.read_policy()
    tests = [value for profile in policy['profiles'].values() for value in profile['tests']]
    assert 'tests/test_delta_validation_v4.py' in tests
    assert 'tests/test_engine_pv.py' not in tests
    assert not any(any(word in name for word in ('onenote', 'visio', 'access_profile')) for name in tests)
    assert not policy['project_defaults']
    assert not policy['coverage']['full_retained_format_coverage']
    assert not policy['coverage']['installed_native_coverage']
    with (ROOT / 'pyproject.toml').open('rb') as stream:
        project = tomllib.load(stream)['project']
    assert (ROOT / 'requirements.in').read_text().splitlines() == project['dependencies']
    runtime = {line for line in (ROOT / 'requirements.lock.txt').read_text().splitlines() if line and not line.startswith('#')}
    development = set((ROOT / 'requirements-dev.lock.txt').read_text().splitlines())
    assert runtime <= development
    assert all('--hash=sha256:' in line for line in development if line and not line.startswith('#'))
    wheel_map = json.loads((ROOT / 'contracts/runtime-lock.win-amd64-cp314.json').read_bytes())
    assert len(wheel_map['dependency_sets']['development']) == len([line for line in development if line and not line.startswith('#')])
    assert 'llama-index-core' in wheel_map['dependency_sets']['development']


def test_ci_rejects_malformed_raw_toml_even_when_newline_normalization_would_hide_it(tmp_path):
    policy = ci.read_policy()
    (tmp_path / '.github').mkdir()
    (tmp_path / ci.POLICY).write_bytes((ROOT / ci.POLICY).read_bytes())
    (tmp_path / 'tests').mkdir()
    for profile in policy['profiles'].values():
        for name in profile['tests']:
            (tmp_path / name).write_text('# Only file existence is needed by the selector.\n')
    original = (ROOT / 'pyproject.toml').read_bytes().replace(b'\r\n', b'\n')
    malformed = original.replace(b'\n', b'\r\r\n')
    (tmp_path / 'pyproject.toml').write_bytes(malformed)
    assert tomllib.loads((tmp_path / 'pyproject.toml').read_text())
    with pytest.raises(tomllib.TOMLDecodeError):
        ci.read_policy(tmp_path)


def test_changes_choose_relevant_profiles_and_unknown_changes_expand_coverage():
    policy = ci.read_policy()
    assert ci.select_profiles(policy, ['README.md'])['profiles'] == ['ci']
    plan = ci.select_profiles(policy, ['plugins/evidence-lane-plugin/src/evidence_lane_plugin/acceptance.py'])
    assert set(plan['profiles']) == {'ci', 'engine', 'plan', 'protocol', 'storage'} | {
        name for name in policy['profiles'] if name.startswith('format_')}
    tableau = ci.select_profiles(policy, ['plugins/evidence-lane-plugin/src/evidence_lane_plugin/tableau_hyper.py'])
    assert set(tableau['profiles']) == {'ci', 'format_tableau', 'engine', 'protocol', 'package'}
    document = ci.select_profiles(policy, ['tests/test_document_profile_v4.py'])
    assert set(document['profiles']) == {'ci', 'format_documents', 'engine', 'protocol', 'package'}
    unknown = ci.select_profiles(policy, ['new-owner/schema.json'])
    assert set(unknown['profiles']) == set(policy['profiles'])
    assert unknown['path_decisions'][0]['basis'] == 'conservative_fallback'
    assert ci.select_profiles(policy, [], full=True)['full_regression_selected']


@pytest.mark.parametrize('path', ['../outside', '/absolute', 'tests\\fake.py', 'x\nprofile=bad'])
def test_untrusted_changed_paths_cannot_inject_selector_or_actions_output(path):
    with pytest.raises(ValueError):
        ci.select_profiles(ci.read_policy(), [path])


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, 'init', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'CI Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'old name.py').write_text('one')
    (tmp_path / 'deleted.py').write_text('two')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'before')
    base = git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / 'old name.py').rename(tmp_path / 'new name.py')
    (tmp_path / 'deleted.py').unlink()
    (tmp_path / 'added.py').write_text('three')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'after')
    return tmp_path, base, git(tmp_path, 'rev-parse', 'HEAD')


def test_real_git_diff_keeps_deletion_and_both_sides_of_rename(repository):
    root, base, head = repository
    actual, paths, full, basis = ci.event_changes(root, 'push', {'before': base}, head)
    assert actual == head and not full and basis == 'git_committed_diff'
    assert paths == ['added.py', 'deleted.py', 'new name.py', 'old name.py']
    # The PR route compares the source branch against its common ancestor.
    event = {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}
    assert ci.event_changes(root, 'pull_request', event, head)[1] == paths


def test_unavailable_comparison_cannot_turn_into_empty_green_check(repository):
    root, _, head = repository
    for base in ('0' * 40, '1' * 40, '--output=outside'):
        assert ci.event_changes(root, 'push', {'before': base}, head)[2] is True
    assert ci.event_changes(root, 'workflow_dispatch', {}, head)[2] is True
    with pytest.raises(ValueError, match='checkout'):
        ci.event_changes(root, 'push', {'before': head}, 'a' * 40)


@pytest.mark.parametrize('body, expected', [
    ('def test_probe():\n    assert True\n', 'passed'),
    ('def test_probe():\n    assert False\n', 'failed'),
    ('import pytest\ndef test_probe():\n    pytest.skip("Unavailable required tool")\n', 'failed'),
    ('from pathlib import Path\ndef test_probe():\n    Path(__file__).write_text("changed source")\n', 'failed'),
])
def test_real_runner_reports_execution_skip_and_source_mutation(tmp_path, body, expected):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_probe.py').write_text(body)
    (tmp_path / '.github').mkdir()
    (tmp_path / ci.POLICY).write_text('{}')
    for name in ('pyproject.toml', 'requirements.in', 'requirements-dev.in', 'requirements.lock.txt', 'requirements-dev.lock.txt'):
        (tmp_path / name).write_text('')
    policy = {'profiles': {'probe': {'tests': ['tests/test_probe.py'], 'timeout_seconds': 30}}, 'coverage': {'scope': 'fixture'}}
    result = ci.run_profile(tmp_path, policy, 'probe')
    receipt = json.loads((tmp_path / '.work/ci/probe/receipt.json').read_bytes())
    assert receipt['status'] == expected
    assert receipt['descendants_joined']
    assert result == (0 if expected == 'passed' else 1)
    assert not receipt['installed_native_verified']
    if 'write_text' in body:
        assert not receipt['source_hashes_unchanged']
        assert (tmp_path / 'tests/test_probe.py').read_text() == 'changed source'
    if 'skip' in body:
        assert receipt['counts']['skipped'] == 1
    before = (tmp_path / '.work/ci/probe/receipt.json').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        ci.run_profile(tmp_path, policy, 'probe')
    assert (tmp_path / '.work/ci/probe/receipt.json').read_bytes() == before


def test_unknown_legacy_profile_is_rejected_before_any_execution(tmp_path):
    with pytest.raises(ValueError, match='legacy'):
        ci.run_profile(tmp_path, ci.read_policy(), 'governed-lifecycle')
    assert not list(tmp_path.iterdir())


def test_workflow_uses_pinned_read_only_windows_runtime_and_literal_profiles():
    # BaseLoader keeps GitHub's YAML 'on' key as a string (YAML 1.1 SafeLoader does not).
    path = ROOT / '.github/workflows/evidence-lane-ci.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['on']) == {'workflow_dispatch', 'push', 'pull_request'}
    assert 'pull_request_target' not in workflow['on']
    for job in workflow['jobs'].values():
        assert job['runs-on'] == 'windows-latest'
        for step in job['steps']:
            reference = step.get('uses')
            if reference and not reference.startswith('./'):
                assert re.fullmatch(r'[\w/-]+@[a-f0-9]{40}', reference)
            if reference and reference.startswith('actions/checkout@'):
                assert step['with']['persist-credentials'] == 'false'
            if reference and reference.startswith('actions/setup-python@'):
                assert step['with']['python-version'] == '3.14.2'
    commands = '\n'.join(step.get('run', '') for job in workflow['jobs'].values() for step in job['steps'])
    assert '--require-hashes' in commands and '--only-binary=:all:' in commands
    assert 'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }' in commands
    assert 'requirements.torch' not in commands
    assert 'github.event.' not in commands
    assert 'python -B -m ruff check --no-cache .' in commands
    assert 'python -B -m mypy --no-incremental' in commands
    with (ROOT / 'pyproject.toml').open('rb') as stream:
        configuration = tomllib.load(stream)
    assert 'mypy==2.3.1' in configuration['project']['optional-dependencies']['dev']
    assert configuration['tool']['mypy']['files'] == ['plugins/evidence-lane-plugin/src/evidence_lane_plugin']
    assert not configuration['tool']['mypy'].get('exclude')
    assert not configuration['tool']['mypy'].get('ignore_errors', False)
    assert not configuration['tool']['mypy'].get('disable_error_code')
    adapter = (ROOT / '.github/actions/evidence-lane-ci/src/main.mjs').read_text()
    assert 'shell: false' in adapter and 'scripts/maintainer_ci.py' in adapter
    assert 'CODE_MODE_CONTRACT' not in adapter and 'test_engine_pv.py' not in adapter
    preview_text = (ROOT / '.github/workflows/evidence-lane-preview-build.yml').read_text()
    preview = yaml.load(preview_text, Loader=yaml.BaseLoader)
    assert 'if' not in preview['jobs']['preview-build']
    assert 'docker build' not in preview_text and 'profile: package' in preview_text
    assert 'scripts/build_studio.py' in preview_text


def test_original_action_pin_and_runtime_closure_guards_cover_current_workflows(tmp_path):
    from evidence_lane_plugin.github_automation_governance import (
        audit_workflow_action_pins,
        audit_workflow_action_runtimes,
    )
    workflows = ROOT / '.github/workflows'
    assert audit_workflow_action_pins(workflows)['status'] == 'PASS'
    runtime = audit_workflow_action_runtimes(workflows, ROOT / '.github/action-runtime-lock.json', repository_root=ROOT)
    assert runtime['status'] == 'PASS', runtime['violations']
    assert runtime['node20_count'] == 0
    (tmp_path / 'unsafe.yml').write_text('jobs:\n  unsafe:\n    steps:\n      - uses: actions/checkout@main\n')
    assert audit_workflow_action_pins(tmp_path)['status'] == 'BLOCKED'
