"""Owning source retirement through actual Delta execution and lane storage."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.code_profile import _snapshot, read_current
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.migrations import read_compatibility
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.selector_schema import selector_migrations

from tests.test_code_profile_v4 import call, code_system
from tests.test_tabular_profile_v4 import execute

__all__ = ['code_system']


def plan(system, actions):
    engine, store, _ = system
    tasks = [TaskDefinition(task_id='table-' + str(index), title=action,
        requested_outcome='Verify owning current selections and preserve history',
        profile=engine.registry.get(action).profile, allowed_actions=[action], permitted_paths=['.'],
        permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser', 'Git'],
        acceptance_checks=list(engine.registry.get(action).verification_checks))
        for index, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Source retirement', tasks=tasks), lease, actor_id='fixture')


def test_deleted_source_leaves_current_history_survives_and_reappearance_advances(code_system):
    engine, store, _ = code_system
    plan(code_system, ['code_index', 'code_index', 'source_snapshot_retire', 'code_index'])
    first = execute(code_system, 'code_index', {'paths': ['helper.py']})['snapshot_id']
    other = execute(code_system, 'code_index', {'paths': ['app.py']}, index=1)['snapshot_id']
    lane = store.lane('local_code')
    assert read_compatibility(lane, selector_migrations('local_code'))[0]['status'] == 'not_initialized'
    before = {str(p.relative_to(store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.root.rglob('*') if p.is_file()}
    state = call(code_system, 'source_snapshot_state', {'lane_id': 'local_code', 'snapshot_id': first})
    assert state.status == 'ok', state.error
    assert state.result['result']['active'] and not state.result['result']['retired']
    assert before == {str(p.relative_to(store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.root.rglob('*') if p.is_file()}
    original = (store.source_root / 'helper.py').read_bytes()
    (store.source_root / 'helper.py').unlink()
    retired = execute(code_system, 'source_snapshot_retire', {'lane_id': 'local_code', 'snapshot_id': first}, index=2)
    assert retired['retired'] and not retired['historical_bytes_deleted']
    assert {row['snapshot_id'] for row in read_current(store, 'local_code')['scopes']} == {other}
    owner = engine.registry.selector_owner('local_code')
    selected = owner.snapshot(store, first)
    assert selected['retired'] and not selected['active'] and selected['head_snapshot'] == first
    assert lane.read_object(selected['files'][0]['sha256']) == original
    (store.source_root / 'helper.py').write_bytes(original)
    refreshed = execute(code_system, 'code_index', {'paths': ['helper.py'], 'expected_snapshot': first}, index=3)
    assert refreshed['snapshot_id'] != first
    _, manifest = _snapshot(lane, refreshed['snapshot_id'])
    assert manifest['generation'] == 2 and manifest['previous_snapshot'] == first
    assert owner.snapshot(store, first)['retired']
    assert owner.snapshot(store, refreshed['snapshot_id'])['active']


def test_changed_scope_retirement_requires_complete_current_replacement(code_system):
    engine, store, _ = code_system
    plan(code_system, ['code_index', 'code_index', 'source_snapshot_retire'])
    first = execute(code_system, 'code_index', {'paths': ['helper.py', 'app.py']})['snapshot_id']
    (store.source_root / 'helper.py').unlink()
    (store.source_root / 'app.py').write_text('answer = 43\n', encoding='utf-8')
    replacement = execute(code_system, 'code_index', {'paths': ['app.py']}, index=1)['snapshot_id']
    retired = execute(code_system, 'source_snapshot_retire', {'lane_id': 'local_code', 'snapshot_id': first,
        'replacements': [{'lane_id': 'local_code', 'snapshot_id': replacement}]}, index=2)
    proof = json.loads(store.lane('local_code').read_object(retired['proof_object']))
    assert {row['path']: row['disposition'] for row in proof['coverage']['files']} == {
        'helper.py': 'absent_at_observation', 'app.py': 'covered_by_replacement'}
    assert engine.registry.selector_owner('local_code').snapshot(store, replacement)['active']


@pytest.mark.parametrize('case,code', [
    ('present', 'SOURCE_SELECTOR_REPLACEMENT_REQUIRED'),
    ('budget', 'SOURCE_SELECTOR_BYTE_BUDGET'),
])
def test_rejected_retirement_preserves_current_and_creates_no_retirement(code_system, case, code):
    engine, store, _ = code_system
    plan(code_system, ['code_index', 'source_snapshot_retire'])
    first = execute(code_system, 'code_index', {'paths': ['helper.py']})['snapshot_id']
    request = {'lane_id': 'local_code', 'snapshot_id': first}
    if case == 'budget':
        request['max_total_bytes'] = 1
    blocked = execute(code_system, 'source_snapshot_retire', request, index=1, expected='blocked')
    assert blocked['error_code'] == code
    assert engine.registry.selector_owner('local_code').snapshot(store, first)['active']
    assert read_compatibility(store.lane('local_code'), selector_migrations('local_code'))[0]['status'] == 'not_initialized'


def test_selector_schema_corruption_is_rejected_without_repair(code_system):
    _, store, _ = code_system
    plan(code_system, ['code_index'])
    execute(code_system, 'code_index', {'paths': ['helper.py']})
    lane = store.lane('local_code')
    # Deliberate isolated corruption: an unowned table must never be treated as
    # an absent optional namespace or silently repaired by a current read.
    with lane.transaction() as connection:
        connection.execute('CREATE TABLE selector_retirement(snapshot_id TEXT)')
    with pytest.raises(LaneError, match='version or ownership'):
        read_current(store, 'local_code')


def test_retirement_verifier_honors_the_execution_stop_before_reading(code_system):
    def stop():
        raise LaneError('DELTA_STOPPED', 'The executing task has stopped.')
    with pytest.raises(LaneError, match='stopped'):
        code_system[0].registry.get('source_snapshot_retire').verifier(SimpleNamespace(check=stop), None, None)
