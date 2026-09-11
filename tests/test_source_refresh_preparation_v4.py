"""Complete source refresh uses the existing Plan and owning Delta coordinator."""
from __future__ import annotations

import hashlib

from evidence_lane_plugin.code_profile import read_current
from evidence_lane_plugin.plan_runtime import PlanStore

from tests.test_code_profile_v4 import call, code_system
from tests.test_source_materialization_v4 import finished, setup, start
from tests.test_source_routing_v4 import registered

__all__ = ['code_system']


def completed_sources(system, *, group_size=1):
    prepared, request = setup(system, group_size=group_size)
    result = finished(system, start(system, request))
    assert result['state'] == 'completed', result
    return prepared, {row['path']: row['snapshot_id'] for row in result['outcome']['files']}


def prepare_refresh(system, snapshots, paths=None, *, max_files=1):
    values = {'baseline_snapshots': [{'lane_id': 'local_code', 'snapshot_id': snapshot}
        for snapshot in dict.fromkeys(snapshots)]}
    if paths is not None:
        route = registered(system, paths)
        values.update(selection={'route_id': route['route_id'], 'occurrence_ordinals': list(range(1, len(paths) + 1))},
            lane_options={'local_code': {'max_files': max_files}})
    return call(system, 'source_prepare_refresh', values)


def adopt_refresh(system, prepared):
    before = PlanStore(system[1]).snapshot()
    assert before.counts == {'completed': before.total_tasks}
    message = 'Refresh the selected source inputs using these prepared tasks.'
    captured = call(system, 'lineage_record', {'kind': 'prompt', 'payload': {'text': message}})
    assert captured.status == 'ok', captured.error
    steer = call(system, 'steer_submit', {'source_event_id': captured.result['event_id'],
        'source_cursor': captured.result['cursor'], 'source_text': message,
        'expected_revision': before.revision, 'intent': 'semantic', 'rationale': message})
    assert steer.status == 'ok' and steer.result['state'] in {'pending', 'ready'}, steer
    changed = call(system, 'plan_refresh', {'plan_id': before.plan_id, 'title': 'Refresh selected sources',
        'expected_revision': before.revision, 'expected_document_digest': before.document_digest,
        'steer_request_id': steer.result['request_id'],
        'tasks': [row.definition.model_dump(mode='json') for row in before.tasks] + prepared['tasks']},
        expected_revision=before.revision)
    assert changed.status == 'ok', changed.error
    after = PlanStore(system[1]).snapshot()
    return {'preparation_id': prepared['preparation_id'], 'plan_revision': after.revision,
        'plan_document_digest': after.document_digest}


def test_changed_new_deleted_refresh_preserves_unselected_scope_and_history(code_system):
    engine, store, _ = code_system
    _, old = completed_sources(code_system)
    untouched = old['package.json']
    (store.source_root / 'helper.py').unlink()
    changed = b'answer = 43\n'
    (store.source_root / 'app.py').write_bytes(changed)
    (store.source_root / 'new.py').write_text('answer = 44\n', encoding='utf-8')
    response = prepare_refresh(code_system, [old['helper.py'], old['app.py']], ['app.py', 'new.py'])
    assert response.status == 'ok', response.error
    prepared = response.result['result']
    assert prepared['file_count'] == 2 and prepared['task_count'] == 3
    assert [row['operation']['action'] for row in prepared['tasks']] == ['code_index', 'code_index', 'source_snapshot_retire']
    assert prepared['tasks'][0]['operation']['arguments']['expected_snapshot'] == old['app.py']
    result = finished(code_system, start(code_system, adopt_refresh(code_system, prepared)))
    assert result['state'] == 'completed', result
    assert result['outcome']['refreshed'] and result['outcome']['materialized']
    assert all(item['verified'] for item in result['outcome']['refresh_baselines'])
    owner = engine.registry.selector_owner('local_code')
    assert owner.snapshot(store, old['helper.py'])['retired']
    assert owner.snapshot(store, untouched)['active']
    assert not owner.snapshot(store, old['app.py'])['active']
    by_path = {row['path']: row for row in result['outcome']['files']}
    assert by_path['app.py']['sha256'] == hashlib.sha256(changed).hexdigest()
    assert len(read_current(store, 'local_code')['scopes']) == 3
    assert PlanStore(store).snapshot().counts == {'completed': 6}
    assert PlanStore(store).verify_history()['events_verified'] > 0


def test_group_membership_change_resolves_replacement_from_verified_task(code_system):
    engine, store, _ = code_system
    _, old = completed_sources(code_system, group_size=32)
    (store.source_root / 'helper.py').unlink()
    response = prepare_refresh(code_system, [old['app.py']], ['app.py', 'package.json'], max_files=32)
    assert response.status == 'ok', response.error
    prepared = response.result['result']
    retirement = prepared['tasks'][-1]['operation']['arguments']
    assert retirement['replacement_tasks'] == [prepared['tasks'][0]['task_id']]
    assert retirement['replacements'] == []
    result = finished(code_system, start(code_system, adopt_refresh(code_system, prepared)))
    assert result['state'] == 'completed', result
    assert engine.registry.selector_owner('local_code').snapshot(store, old['app.py'])['retired']
    assert len(read_current(store, 'local_code')['scopes']) == 1


def test_deletion_only_group_retires_without_claiming_files_were_materialized(code_system):
    engine, store, _ = code_system
    _, old = completed_sources(code_system)
    (store.source_root / 'helper.py').unlink()
    response = prepare_refresh(code_system, [old['helper.py']])
    assert response.status == 'ok', response.error
    prepared = response.result['result']
    assert prepared['selection'] is None and prepared['file_count'] == 0 and prepared['task_count'] == 1
    result = finished(code_system, start(code_system, adopt_refresh(code_system, prepared)))
    assert result['state'] == 'completed', result
    assert result['outcome']['refreshed'] and not result['outcome']['materialized']
    assert result['outcome']['file_count'] == 0
    assert engine.registry.selector_owner('local_code').snapshot(store, old['helper.py'])['retired']


def test_partial_selection_cannot_retire_a_scope_containing_unselected_live_files(code_system):
    engine, store, _ = code_system
    _, old = completed_sources(code_system, group_size=32)
    (store.source_root / 'helper.py').unlink()
    workers = engine.workers.status()['submitted']
    response = prepare_refresh(code_system, [old['app.py']], ['app.py'])
    assert response.status == 'error' and response.error.code == 'SOURCE_REFRESH_INCOMPLETE_SCOPE'
    assert engine.workers.status()['submitted'] == workers
    assert engine.registry.selector_owner('local_code').snapshot(store, old['app.py'])['active']


def test_deleted_file_reappearing_after_verified_retirement_blocks_group_coverage(code_system, monkeypatch):
    engine, store, _ = code_system
    _, old = completed_sources(code_system)
    original = (store.source_root / 'helper.py').read_bytes()
    (store.source_root / 'helper.py').unlink()
    response = prepare_refresh(code_system, [old['helper.py']])
    assert response.status == 'ok', response.error
    request = adopt_refresh(code_system, response.result['result'])
    original_coverage = engine.source_materialization._coverage
    def reappeared(*args, **kwargs):
        (store.source_root / 'helper.py').write_bytes(original)
        return original_coverage(*args, **kwargs)
    monkeypatch.setattr(engine.source_materialization, '_coverage', reappeared)
    result = finished(code_system, start(code_system, request))
    assert result['state'] == 'blocked', result
    assert result['outcome']['error_code'] == 'SOURCE_SELECTOR_REPLACEMENT_REQUIRED'
    assert result['jobs'][0]['state'] == 'verified' and not result['outcome']['materialized']
