"""Git refresh preserves committed blob identity separately from working bytes."""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from tests.test_code_profile_v4 import call, code_system, git_source
from tests.test_source_materialization_v4 import finished, start
from tests.test_source_refresh_preparation_v4 import adopt_refresh
from tests.test_source_routing_v4 import registered
from tests.test_source_selectors_v4 import plan
from tests.test_tabular_profile_v4 import execute

__all__ = ['code_system']


def checkpoint(system):
    route = registered(system, ['.'], code_mode='github_code')
    history = call(system, 'source_git_history', {'batch_id': route['batch_id'], 'occurrence_ordinal': 1})
    assert history.status == 'ok', history.error
    return route, history.result['result']['snapshot_id']


def test_git_group_refresh_and_retirement_use_commit_blobs_with_crlf_working_files(code_system):
    engine, store, _ = code_system
    source = store.source_root
    (source / 'helper.py').write_bytes(b'def greeting(name):\r\n    return "hello " + name\r\n')
    git_source(code_system)
    _, old_git = checkpoint(code_system)
    # Git attributes configure byte conversion; only these three source files
    # are selected for this Code-only worker fixture.
    route = registered(code_system, ['app.py', 'helper.py', 'package.json'], code_mode='github_code')
    response = call(code_system, 'source_prepare_tasks', {'selection': {
        'route_id': route['route_id'], 'occurrence_ordinals': [1, 2, 3]},
        'lane_options': {'github_code': {'git_snapshot_id': old_git}}})
    assert response.status == 'ok', response.error
    initial = response.result['result']
    assert [task['operation']['action'] for task in initial['tasks']] == ['code_index_git']
    assert call(code_system, 'plan_create', {'title': 'Git selected sources', 'tasks': initial['tasks']}).status == 'ok'
    from evidence_lane_plugin.plan_runtime import PlanStore
    old_result = finished(code_system, start(code_system, {'preparation_id': initial['preparation_id'],
        'plan_revision': 1, 'plan_document_digest': PlanStore(store).snapshot().document_digest}))
    assert old_result['state'] == 'completed', (old_result['outcome'], old_result['jobs'])
    helper = next(row for row in old_result['outcome']['files'] if row['path'] == 'helper.py')
    assert helper['sha256'] != helper['worktree_sha256']
    assert not helper['worktree_byte_equality_inferred']
    old_snapshot = helper['snapshot_id']
    (source / 'helper.py').unlink()
    (source / 'app.py').write_bytes(b'answer = 43\r\n')
    (source / 'new.py').write_bytes(b'answer = 44\r\n')
    for arguments in (['add', '.'], ['commit', '-m', 'Refresh selected sources']):
        subprocess.run(['git', '-C', str(source), *arguments], check=True, capture_output=True)
    _, new_git = checkpoint(code_system)
    route = registered(code_system, ['app.py', 'new.py', 'package.json'], code_mode='github_code')
    response = call(code_system, 'source_prepare_refresh', {'selection': {'route_id': route['route_id'], 'occurrence_ordinals': [1, 2, 3]},
        'baseline_snapshots': [{'lane_id': 'github_code', 'snapshot_id': old_snapshot}], 'git_snapshot_id': new_git})
    assert response.status == 'ok', response.error
    prepared = response.result['result']
    assert prepared['tasks'][-1]['operation']['action'] == 'source_snapshot_retire'
    assert prepared['tasks'][-1]['operation']['arguments']['git_snapshot_id'] == new_git
    result = finished(code_system, start(code_system, adopt_refresh(code_system, prepared)))
    assert result['state'] == 'completed', (result['outcome'], result['jobs'])
    app = next(row for row in result['outcome']['files'] if row['path'] == 'app.py')
    assert app['sha256'] == hashlib.sha256(b'answer = 43\n').hexdigest()
    assert app['worktree_sha256'] == hashlib.sha256(b'answer = 43\r\n').hexdigest()
    assert engine.registry.selector_owner('github_code').snapshot(store, old_snapshot)['retired']


@pytest.mark.parametrize('case,code', [('no_checkpoint', 'SOURCE_SELECTOR_GIT_CHECKPOINT_REQUIRED'),
    ('uncommitted_deletion', 'SOURCE_SELECTOR_GIT_CHECKPOINT_CHANGED')])
def test_git_selector_cannot_retire_from_local_file_absence(code_system, case, code):
    engine, store, _ = code_system
    history = git_source(code_system)
    plan(code_system, ['code_index_git', 'source_snapshot_retire'])
    first = execute(code_system, 'code_index_git', {'paths': ['helper.py'], 'git_snapshot_id': history})['snapshot_id']
    (store.source_root / 'helper.py').unlink()
    request = {'lane_id': 'github_code', 'snapshot_id': first}
    if case != 'no_checkpoint':
        request['git_snapshot_id'] = history
    blocked = execute(code_system, 'source_snapshot_retire', request, index=1, expected='blocked')
    assert blocked['error_code'] == code
    assert engine.registry.selector_owner('github_code').snapshot(store, first)['active']
