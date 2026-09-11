"""Changed-source byte evidence through actual registered Git Delta operations."""
from __future__ import annotations

import hashlib
import json

import pytest
from evidence_lane_plugin.enrollment import current_branch_authority
from evidence_lane_plugin.errors import LaneError

from tests import test_git_enrollment_v4 as enrollment_fixture_module
from tests import test_git_sync_v4 as sync_fixture_module
from tests.test_git_enrollment_v4 import args as enrollment_args
from tests.test_git_enrollment_v4 import effects as enrollment_effects
from tests.test_git_enrollment_v4 import plan as enrollment_plan
from tests.test_git_sync_v4 import clone_target, execute, git, plan, selection
from tests.test_remote_git_v4 import run

enrollment_system = enrollment_fixture_module.system
git_system = sync_fixture_module.git_system


def measured(system, result):
    reference = result['result']['source_file_fingerprint']
    return reference, json.loads(system[1].lane('sources').read_object(reference['object_id']))


def test_fast_forward_measures_changed_added_and_deleted_files_without_claiming_reindex(git_system, tmp_path):
    upstream, _ = clone_target(git_system, tmp_path)
    (upstream / 'app.py').unlink()
    raw = b'\x00new binary source\xff'
    (upstream / 'added.bin').write_bytes(raw)
    git(upstream, 'add', '-A')
    git(upstream, 'commit', '-m', 'fixture add and delete')
    plan(git_system)
    output = execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
        expected_commit=git(upstream, 'rev-parse', 'HEAD')))
    reference, fingerprint = measured(git_system, output)
    rows = {row['path']: row for row in fingerprint['entries']}
    assert rows['app.py']['state'] == 'MISSING_WORKTREE_PATH'
    assert rows['added.bin']['current_sha256'] == hashlib.sha256(raw).hexdigest().upper()
    assert reference['scope'] == 'exact_git_changed_paths' and reference['path_count'] == 2
    assert output['result']['source_reindex_required'] and not reference['source_index_refreshed']
    assert [row['state'] for row in enrollment_effects(git_system)] == ['confirmed', 'confirmed']


def test_noop_fetch_has_explicit_empty_changed_path_observation(git_system):
    plan(git_system)
    output = execute(git_system, selection(git_system, mode='fast_forward'))
    reference, fingerprint = measured(git_system, output)
    assert reference['path_count'] == 0 and fingerprint['entries'] == []
    assert not output['result']['source_reindex_required']
    assert [row['state'] for row in enrollment_effects(git_system)] == ['confirmed']


def test_dirty_authority_only_selection_does_not_claim_changed_file_observation(git_system):
    (git_system[1].source_root / 'untracked').write_bytes(b'preserve exactly')
    plan(git_system)
    output = execute(git_system, selection(git_system))
    assert output['result']['source_file_fingerprint'] is None
    assert not output['result']['source_bytes_mutated'] and not enrollment_effects(git_system)


def test_actual_clone_measures_every_enrolled_file(enrollment_system):
    enrollment_plan(enrollment_system)
    output = run(enrollment_system, 'enroll_project', enrollment_args(enrollment_system), 0)
    reference, fingerprint = measured(enrollment_system, output)
    assert reference['scope'] == 'exact_enrolled_tree_paths'
    assert reference['path_count'] == output['result']['file_count'] == 1
    row = fingerprint['entries'][0]
    assert row['path'] == 'app.py'
    assert row['current_sha256'] == hashlib.sha256((enrollment_system[1].source_root / 'app.py').read_bytes()).hexdigest().upper()
    assert output['result']['source_reindex_required'] and not reference['source_index_refreshed']


@pytest.mark.parametrize('stage', ['observation', 'publication'])
def test_failed_fingerprint_keeps_confirmed_fast_forward_and_prior_authority(git_system, tmp_path, monkeypatch, stage):
    import evidence_lane_plugin.enrollment as owner
    upstream, commit = clone_target(git_system, tmp_path)
    plan(git_system, count=2)
    execute(git_system, selection(git_system))
    authority = current_branch_authority(git_system[1])
    function = '_source_file_fingerprint' if stage == 'observation' else '_publish_file_fingerprint'
    original = getattr(owner, function)
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise LaneError('TEST_FINGERPRINT_INTERRUPTED', 'Exact post-effect fingerprint failure fixture')
    monkeypatch.setattr(owner, function, fail)
    execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
        expected_commit=commit, replace_branch_authority=False), index=1, error='TEST_FINGERPRINT_INTERRUPTED')
    assert git(git_system[1].source_root, 'rev-parse', 'HEAD') == commit
    assert current_branch_authority(git_system[1]) == authority
    assert [row['state'] for row in enrollment_effects(git_system)] == ['confirmed', 'confirmed']


def test_hidden_postpublication_file_change_fails_real_git_acceptance(git_system, tmp_path, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    upstream, commit = clone_target(git_system, tmp_path)
    original = owner._publish_file_fingerprint
    def publish(store, fingerprint, scope):
        reference = original(store, fingerprint, scope)
        git(store.source_root, 'update-index', '--assume-unchanged', 'app.py')
        (store.source_root / 'app.py').write_bytes(b'print("unreported changed bytes")\n')
        return reference
    monkeypatch.setattr(owner, '_publish_file_fingerprint', publish)
    plan(git_system)
    execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
        expected_commit=commit), error='DELTA_ACCEPTANCE_FAILED')
    assert git(git_system[1].source_root, 'status', '--porcelain') == ''
    assert [row['state'] for row in enrollment_effects(git_system)] == ['confirmed', 'confirmed']


@pytest.mark.parametrize('operation', ['sync', 'enrollment'])
def test_omitted_file_cannot_pass_an_addressed_manifest_as_full_operation_scope(
        request, tmp_path, monkeypatch, operation):
    import evidence_lane_plugin.enrollment as owner
    original = owner._source_file_fingerprint
    monkeypatch.setattr(owner, '_source_file_fingerprint', lambda context, paths: original(context, []))
    if operation == 'sync':
        git_system = request.getfixturevalue('git_system')
        upstream, commit = clone_target(git_system, tmp_path)
        plan(git_system)
        execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
            expected_commit=commit), error='DELTA_ACCEPTANCE_FAILED')
    else:
        enrollment_system = request.getfixturevalue('enrollment_system')
        enrollment_plan(enrollment_system)
        run(enrollment_system, 'enroll_project', enrollment_args(enrollment_system), 0, error='DELTA_ACCEPTANCE_FAILED')


def test_changed_binary_larger_than_code_parser_cap_retains_git_capacity(git_system, tmp_path):
    upstream, _ = clone_target(git_system, tmp_path)
    raw = b'\x00binary\xff' * 200_000
    assert len(raw) > 1_048_576
    (upstream / 'large.bin').write_bytes(raw)
    git(upstream, 'add', 'large.bin')
    git(upstream, 'commit', '-m', 'fixture binary over parser cap')
    plan(git_system)
    output = execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
        expected_commit=git(upstream, 'rev-parse', 'HEAD')))
    _, fingerprint = measured(git_system, output)
    row = next(row for row in fingerprint['entries'] if row['path'] == 'large.bin')
    assert row['current_bytes'] == len(raw)
    assert row['current_sha256'] == hashlib.sha256(raw).hexdigest().upper()


def test_previous_successful_git_result_cannot_complete_a_new_plan_job(git_system, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    plan(git_system, count=2)
    first = execute(git_system, selection(git_system, mode='fast_forward'))
    previous = owner.GitSyncResult.model_validate(first)
    monkeypatch.setattr(owner, 'GitSyncResult', lambda **_values: previous)
    execute(git_system, selection(git_system, mode='fast_forward', replace_branch_authority=False),
        index=1, error='DELTA_ACCEPTANCE_FAILED')
    assert [row['state'] for row in enrollment_effects(git_system)] == ['confirmed', 'confirmed']


def test_confirmed_effect_with_wrong_sync_proof_fails_acceptance(git_system, tmp_path, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    upstream, commit = clone_target(git_system, tmp_path)
    original = owner._confirm
    def confirm(execution, effect, body):
        original(execution, effect, {**body, 'sync_id': 'different-operation'})
    monkeypatch.setattr(owner, '_confirm', confirm)
    plan(git_system)
    execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
        expected_commit=commit), error='DELTA_ACCEPTANCE_FAILED')


def test_recorded_changed_path_subset_is_compared_with_actual_git_diff(git_system, tmp_path, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    upstream, _ = clone_target(git_system, tmp_path)
    (upstream / 'second.txt').write_bytes(b'also changed')
    git(upstream, 'add', 'second.txt')
    git(upstream, 'commit', '-m', 'fixture second changed source')
    original = owner._source_file_fingerprint
    def omit(context, paths):
        paths.pop()
        return original(context, paths)
    monkeypatch.setattr(owner, '_source_file_fingerprint', omit)
    plan(git_system)
    execute(git_system, selection(git_system, mode='fast_forward', source=str(upstream),
        expected_commit=git(upstream, 'rev-parse', 'HEAD')), error='DELTA_ACCEPTANCE_FAILED')
