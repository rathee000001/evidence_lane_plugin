"""Named Custom instances preserve independent SQLite/file and contract identities."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.custom_lanes import Configure
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lanes import LANE_REGISTRY
from evidence_lane_plugin.sector_evidence_profile import read_snapshot
from pydantic import ValidationError

from tests.test_evidence_sectors_v4 import call, execute, plan, system
from tests.test_evidence_views_v4 import preview, publish

__all__ = ['system']


def configure(system, lane_id, **changes):
    values = {'lane_id': lane_id, 'display_name': lane_id,
        'adapter': {'parser': 'text', 'extensions': ['.md']}, **changes}
    response = call(system, 'custom_lane_configure', values)
    assert response.status == 'ok', response.error
    return response.result['result']


def arguments(lane_id, contract, **changes):
    return {'lane_id': lane_id, 'filename': 'note.md', 'parser': 'text',
        'adapter_contract': contract['contract_sha256'], **changes}


def database_bytes(store):
    result = {path.relative_to(store.root).as_posix(): path.read_bytes() for path in store.root.rglob('*')
        if path.is_file() and path.suffix.lower() in {'.sqlite', '.sqlite3', '.db'}}
    assert 'root-pv.sqlite3' in result
    return result


def test_two_custom_instances_share_parser_but_never_storage_or_selectors(system):
    store = system[1]
    registry_before = tuple(LANE_REGISTRY)
    first = configure(system, 'custom__trials')
    second = configure(system, 'custom__notes')
    route = call(system, 'toolchain_resolve', {'action': 'custom_index', 'arguments': arguments('custom__trials', first)})
    assert route.status == 'ok', route.error
    assert route.result['resolution']['selected_route'] == 'custom_index.native'
    connectors = call(system, 'connector_read', {'max_bytes': 262_144})
    assert connectors.status == 'ok', connectors.error
    assert {'custom__trials', 'custom__notes'} <= set(connectors.result['scopes']['lanes'])
    plan(system, 'custom', ['custom_index', 'custom_index', 'custom_refresh'])
    a = execute(system, 'custom_index', arguments('custom__trials', first))
    b = execute(system, 'custom_index', arguments('custom__notes', second), index=1)
    assert a['snapshot_id'] != b['snapshot_id'] and a['source_id'] != b['source_id']
    lane_a, lane_b = store.lane('custom__trials'), store.lane('custom__notes')
    manifest_a, facts_a = read_snapshot(store, lane_a.lane_id, a['snapshot_id'])
    manifest_b, facts_b = read_snapshot(store, lane_b.lane_id, b['snapshot_id'])
    assert manifest_a['adapter_contract'] == first['contract_sha256']
    assert manifest_a['raw_object'] == manifest_b['raw_object']
    assert {item['item_id'] for item in facts_a['items']}.isdisjoint(item['item_id'] for item in facts_b['items'])
    assert lane_a.database != lane_b.database and lane_a.files != lane_b.files
    assert lane_a.object_path(manifest_a['raw_object']).read_bytes() == lane_b.object_path(manifest_b['raw_object']).read_bytes()
    assert not (store.root / 'sectors/custom').exists()
    before = database_bytes(store)
    for lane_id, snapshot in ((lane_a.lane_id, a), (lane_b.lane_id, b)):
        searched = call(system, 'lane_search', {'lane_id': lane_id, 'query': 'exact source'})
        assert searched.status == 'ok', searched.error
        assert searched.result['arms'][0]['state'] == 'hit'
        assert searched.result['arms'][0]['queries'][0]['arguments']['lane_id'] == lane_id
        read = call(system, 'lane_fetch', {'lane_id': lane_id, 'snapshot_id': snapshot['snapshot_id'],
            'path': 'note.md', 'representation': 'original_source'})
        assert read.status == 'ok', read.error
        assert base64.b64decode(read.result['result']['read']['result']['content_base64']) == (store.source_root / 'note.md').read_bytes()
        status = call(system, 'lane_status', {'lane_id': lane_id})
        assert status.status == 'ok', status.error
        assert status.result['result']['database_head_verified']
    assert database_bytes(store) == before
    combined = call(system, 'search', {'query': 'exact source', 'max_bytes': 262_144})
    assert combined.status == 'ok', combined.error
    assert {arm['lane_id'] for arm in combined.result['arms'] if arm['state'] == 'hit'} >= {lane_a.lane_id, lane_b.lane_id}
    assert database_bytes(store) == before
    other_before = lane_b.database.read_bytes()
    original = (store.source_root / 'note.md').read_bytes()
    (store.source_root / 'note.md').write_text('Decision: changed trial only\n', encoding='utf-8')
    updated = execute(system, 'custom_refresh', arguments(lane_a.lane_id, first, expected_snapshot=a['snapshot_id']), index=2)
    assert updated['previous_snapshot'] == a['snapshot_id']
    assert lane_b.database.read_bytes() == other_before
    assert call(system, 'custom_current', {'lane_id': lane_b.lane_id}).result['result']['files'][0]['snapshot_id'] == b['snapshot_id']
    historical = call(system, 'custom_read', {'lane_id': lane_a.lane_id, 'snapshot_id': a['snapshot_id']})
    assert base64.b64decode(historical.result['result']['content_base64']) == original
    foreign = call(system, 'custom_read', {'lane_id': lane_b.lane_id, 'snapshot_id': a['snapshot_id']})
    assert foreign.status == 'error' and foreign.error.code == 'SECTOR_EVIDENCE_SNAPSHOT_MISSING'
    with store.connection(read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        heads = {row[0] for row in connection.execute('SELECT lane_id FROM root_lane_heads')}
    assert not any(table.startswith(('custom_', 'customlanes_')) for table in tables)
    assert {lane_a.lane_id, lane_b.lane_id} <= heads
    assert tuple(LANE_REGISTRY) == registry_before


def test_named_custom_source_routes_prepare_an_exact_adapter_bound_task(system):
    from tests.test_source_routing_v4 import registered
    lane_id = 'custom__trials'
    contract = configure(system, lane_id)
    path = str(system[1].source_root / 'note.md')
    route = registered(system, ['note.md'], overrides={path: lane_id})
    routed = call(system, 'source_routes_read', {'route_id': route['route_id']})
    assert routed.status == 'ok' and routed.result['result']['routes'][0]['lane_id'] == lane_id
    before_recipe = database_bytes(system[1])
    workflow = call(system, 'project_workflow_configure', {'batch_id': route['batch_id'], 'requested_outcome': 'Inspect named trial evidence'})
    assert workflow.status == 'ok', workflow.error
    assert workflow.result['selected_sector_lanes'] == [lane_id]
    assert workflow.result['project_class_policy']['selected_lanes'] == [lane_id]
    assert workflow.result['project_class_policy']['selected_lanes_outside_class_defaults'] == []
    assert database_bytes(system[1]) == before_recipe
    prepared = call(system, 'source_prepare_tasks', {'selection': {
        'route_id': route['route_id'], 'occurrence_ordinals': [1]},
        'lane_options': {lane_id: {'parser': 'text', 'adapter_contract': contract['contract_sha256']}}})
    assert prepared.status == 'ok', prepared.error
    task = prepared.result['result']['tasks'][0]
    assert task['profile'] == 'custom' and task['operation']['action'] == 'custom_index'
    assert task['operation']['arguments']['lane_id'] == lane_id
    assert task['operation']['arguments']['adapter_contract'] == contract['contract_sha256']
    created = call(system, 'plan_create', {'title': 'Named Custom source intake', 'tasks': [task]})
    assert created.status == 'ok', created.error
    from evidence_lane_plugin.plan_runtime import PlanStore

    from tests.test_source_routing_v4 import finished
    selected = PlanStore(system[1]).task(task['task_id'], expected_revision=1)
    from types import SimpleNamespace

    from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
    from evidence_lane_plugin.sdk import EvidenceLaneClient

    from tests.test_sector_package_owners_v4 import package_module
    sdk = PublicActionSDKDispatcher(system[0])
    client = EvidenceLaneClient(SimpleNamespace(send=lambda request: sdk.execute(request, system[2])))
    outcome = finished(system, package_module('custom', 'builder.py').build_lane_sources(client,
        project_id=system[1].project_id, task_id=task['task_id'], plan_revision=1, contract_digest=selected.contract_digest))
    assert outcome['result']['lane_id'] == lane_id
    assert PlanStore(system[1]).snapshot().counts == {'completed': 1}
    current = call(system, 'custom_current', {'lane_id': lane_id}).result['result']['files'][0]
    manifest = read_snapshot(system[1], lane_id, current['snapshot_id'])[0]
    assert manifest['source_route'] == task['operation']['source_route']
    reader = package_module('custom', 'reader.py')
    read = reader.read_lane_source(client, project_id=system[1].project_id, action='custom_query',
        arguments={'lane_id': lane_id, 'snapshot_id': current['snapshot_id'], 'query': 'exact source'})
    assert read.status == 'ok' and read.result['result']['rows'], read.error
    assert package_module('custom', 'runtime.py').inspect(system[1], lane_id=lane_id)['files'][0]['snapshot_id'] == current['snapshot_id']
    import jsonschema

    from tests.test_sector_package_owners_v4 import PLUGIN
    schema = json.loads((PLUGIN / 'authorities/project_sectors/custom/reader-contract.schema.json').read_bytes())
    jsonschema.Draft202012Validator(schema).validate({'action': 'custom_current',
        'project_id': system[1].project_id, 'arguments': {'lane_id': lane_id}})


def test_named_custom_pointer_and_retirement_remain_in_their_own_lane(system, monkeypatch):
    from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
    lane_id = 'custom__trials'
    contract = configure(system, lane_id)
    engine, store, _ = system
    tasks = [TaskDefinition(task_id='evidence-' + str(index), title=action,
        requested_outcome='Verify independent source selection', profile=profile,
        allowed_actions=[action], permitted_tools=['Python', 'SQLite_FTS5_BM25'], permitted_paths=['.'],
        acceptance_checks=list(engine.registry.get(action).verification_checks))
        for index, (action, profile) in enumerate((('custom_index', 'custom'), ('source_snapshot_retire', 'sources')))]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Named Custom retirement', tasks=tasks), lease, actor_id='fixture')
    indexed = execute(system, 'custom_index', arguments(lane_id, contract))
    before = database_bytes(store)
    chosen = preview(system, lane_id)
    assert database_bytes(store) == before
    assert all(node['locator']['lane_id'] == lane_id for node in chosen['graph']['nodes'])
    assert chosen['source_head']['schemas'][0]['status'] == 'compatible'
    published = publish(system, lane_id, chosen, formats=(), pointer=True)
    assert all('/sectors/' + lane_id + '/' in row['path'].replace('\\', '/') for row in published['files'])
    pointer = call(system, 'lane_view_read', {'view_id': lane_id + '.structure', 'include_content': True})
    assert pointer.status == 'ok' and pointer.result['state'] == 'fresh', pointer.error
    values = json.loads(pointer.result['contents']['pointer'])['items_and_schema'].values()
    assert all(value['lane_id'] == lane_id for value in values)
    catalog = call(system, 'lane_view_catalog', {'view_ids': [lane_id + '.structure']})
    assert catalog.status == 'ok' and catalog.result['views'][0]['lane_id'] == lane_id
    from evidence_lane_plugin import sector_evidence_views
    producer_file = Path(sector_evidence_views.__file__).resolve()
    original_read_bytes = Path.read_bytes
    def changed_producer_bytes(path):
        original = original_read_bytes(path)
        return original + b'\n# changed graph implementation\n' if path.resolve() == producer_file else original
    before = database_bytes(store)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', changed_producer_bytes)
        changed = call(system, 'lane_view_read', {'view_id': lane_id + '.structure'})
        assert changed.status == 'ok' and changed.result['state'] == 'contract_changed', changed.error
    assert database_bytes(store) == before
    assert call(system, 'lane_view_read', {'view_id': lane_id + '.structure'}).result['state'] == 'fresh'
    (store.source_root / 'note.md').unlink()
    execute(system, 'source_snapshot_retire', {'lane_id': lane_id, 'snapshot_id': indexed['snapshot_id']}, index=1)
    assert call(system, 'custom_current', {'lane_id': lane_id}).result['result']['files'] == []
    assert call(system, 'custom_read', {'lane_id': lane_id, 'snapshot_id': indexed['snapshot_id']}).status == 'ok'
    assert call(system, 'lane_view_read', {'view_id': lane_id + '.structure'}).result['state'] == 'stale'


def test_custom_configuration_failure_preserves_the_previous_sources_contract(system, monkeypatch):
    from evidence_lane_plugin.storage import ProjectStore
    contract = configure(system, 'custom__trials')
    store = system[1]
    before = {lane: store.lane(lane).database.read_bytes() for lane in ('sources', 'custom__trials')}
    original = ProjectStore.append_receipt
    def fail(self, kind, *args, **kwargs):
        if kind == 'custom_lane_configured':
            raise LaneError('CUSTOM_FIXTURE_PUBLICATION_FAILURE', 'Injected receipt failure before coordinated publication.')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ProjectStore, 'append_receipt', fail)
    failed = call(system, 'custom_lane_configure', {'lane_id': 'custom__trials', 'display_name': 'Rejected update',
        'adapter': {'parser': 'text', 'extensions': ['.md']}, 'expected_contract': contract['contract_sha256']})
    assert failed.status == 'error' and failed.error.code == 'CUSTOM_FIXTURE_PUBLICATION_FAILURE'
    assert {lane: store.lane(lane).database.read_bytes() for lane in before} == before
    assert call(system, 'custom_lanes_read', {'lane_id': 'custom__trials'}).result['result']['rows'] == [contract]


def test_custom_registration_preserves_an_existing_unregistered_database(system):
    from evidence_lane_plugin.lanes import get_lane
    store = system[1]
    target = store.root / get_lane('custom__collision').database_relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b'Existing user bytes remain unchanged')
    response = call(system, 'custom_lane_configure', {'lane_id': 'custom__collision', 'display_name': 'Collision',
        'adapter': {'parser': 'text', 'extensions': ['.md']}})
    assert response.status == 'error' and response.error.code == 'UNREGISTERED_LANE_DATABASE'
    assert target.read_bytes() == b'Existing user bytes remain unchanged'
    assert call(system, 'custom_lanes_read').result['result']['rows'] == []


def test_custom_instances_keep_the_original_sealed_metadata_schema_compiler(system):
    from tests.test_custom_source_schema_v4 import _definition
    from tests.test_source_routing_v4 import registered
    lane_id = 'custom__trials'
    configure(system, lane_id)
    store = system[1]
    (store.source_root / 'src').mkdir()
    (store.source_root / 'src/member.py').write_text('print("source metadata only")\n', encoding='utf-8')
    route = registered(system, ['.'], overrides={str(store.source_root): lane_id})
    definition = _definition()
    definition['target_lane'] = lane_id
    definition['selectors'][0]['lane_ids'] = [lane_id]
    definition['fields'][-1]['literal'] = lane_id
    before = store.lane(lane_id).database.read_bytes()
    mapped = call(system, 'source_schema_map', {'batch_id': route['batch_id'], 'definition': definition})
    assert mapped.status == 'ok', mapped.error
    assert mapped.result['result']['mapping_count'] == 1
    assert mapped.result['result']['mappings'][0]['mapped_fields']['target_lane'] == lane_id
    assert store.lane(lane_id).database.read_bytes() == before
    assert call(system, 'custom_current', {'lane_id': lane_id}).result['result']['files'] == []


def test_custom_instance_and_pointer_survive_coherent_backup(system, tmp_path):
    from pathlib import Path

    from evidence_lane_plugin.database_recovery import (
        BackupRequest,
        DatabaseRecovery,
        verify_backup,
    )
    from evidence_lane_plugin.storage import ProjectStore
    lane_id = 'custom__trials'
    contract = configure(system, lane_id)
    engine, store, session = system
    plan(system, 'custom', ['custom_index'])
    indexed = execute(system, 'custom_index', arguments(lane_id, contract))
    view = publish(system, lane_id, formats=(), pointer=True)
    before = store.lane(lane_id).database.read_bytes()
    with engine.project_work.mutation(store) as lease:
        backup = DatabaseRecovery(engine, store).backup(BackupRequest(destination_root=str(tmp_path / 'backup')),
            lease, actor_id=session.client_id)
    manifest = verify_backup(backup.backup_root, backup.manifest_digest, store.project_id)
    assert manifest['root_pv'] == backup.root_pv
    copied = ProjectStore(Path(backup.backup_root) / 'payload', read_only=True)
    assert copied.lane(lane_id).database.read_bytes() == before
    assert read_snapshot(copied, lane_id, indexed['snapshot_id'])[0]['adapter_contract'] == contract['contract_sha256']
    assert any(row['path'].startswith('sectors/' + lane_id + '/') and row['path'].endswith('lane_pointer.json') for row in manifest['files'])
    assert view['snapshot_digest']


@pytest.mark.parametrize('change', ['scope', 'implementation'])
def test_custom_adapter_rejects_scope_and_implementation_changes_before_parsing(system, monkeypatch, change):
    from evidence_lane_plugin import sector_evidence_profile
    lane_id = 'custom__trials'
    contract = configure(system, lane_id)
    plan(system, 'custom', ['custom_index'])
    values = arguments(lane_id, contract)
    if change == 'scope':
        values['parser'] = 'opaque'
    else:
        monkeypatch.setattr(sector_evidence_profile, 'parser_contract', lambda _parser: '0' * 64)
    execute(system, 'custom_index', values, expected='blocked')
    assert system[0].workers.status()['succeeded_operations'] == 0
    assert call(system, 'custom_current', {'lane_id': lane_id}).result['result']['files'] == []


def test_custom_registration_is_versioned_and_does_not_overwrite_existing_lane(system):
    first = configure(system, 'custom__trials')
    duplicate = configure(system, 'custom__trials')
    assert duplicate == first
    updated = configure(system, 'custom__trials', display_name='Updated trials', expected_contract=first['contract_sha256'])
    assert updated['previous_contract'] == first['contract_sha256']
    stale = call(system, 'custom_lane_configure', {'lane_id': 'custom__trials', 'display_name': 'Stale',
        'adapter': {'parser': 'text', 'extensions': ['.md']}, 'expected_contract': first['contract_sha256']})
    assert stale.status == 'error' and stale.error.code == 'CUSTOM_LANE_CONTRACT_CHANGED'
    historical = call(system, 'custom_lanes_read', {'lane_id': 'custom__trials', 'contract_sha256': first['contract_sha256']})
    assert historical.status == 'ok' and historical.result['result']['rows'] == [first]
    plan(system, 'custom', ['custom_index'])
    rejected = execute(system, 'custom_index', arguments('custom__trials', first), expected='blocked')
    assert rejected['state'] == 'blocked'
    assert call(system, 'custom_current', {'lane_id': 'custom__trials'}).result['result']['files'] == []
    assert system[0].workers.status()['succeeded_operations'] == 0


@pytest.mark.parametrize('lane_id', ['custom', 'Custom__trial', 'custom__', 'custom__../plan', 'plan', 'custom__a/b'])
def test_custom_registration_requires_exact_safe_instance_identity(lane_id):
    with pytest.raises(ValidationError):
        Configure(lane_id=lane_id, display_name='Invalid', adapter={'parser': 'text', 'extensions': ['.md']})
