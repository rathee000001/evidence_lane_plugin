import hashlib
import json

from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.writers import WriterLease

from tests import test_studio
from tests.test_studio import add_project, login

studio = test_studio.studio


def test_built_assets_and_owner_queries_cannot_open_mutation_route(studio, tmp_path):
    engine, endpoint, client = studio
    manifest = __import__('pathlib').Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src/evidence_lane_plugin/studio/assets-manifest.json'
    document = json.loads(manifest.read_text(encoding='utf-8'))
    assert len(document['assets']) >= 6
    for route, asset in document['assets'].items():
        response = client.get(route)
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == asset['sha256']
        assert response.headers['content-type'] == asset['content_type']
    bundle = b''.join(client.get(route).content for route in document['assets'] if route.endswith(('.js', '.html')))
    for phrase in (b'Add project', b'Request cancel', b'Add plugin grant',
                   b'Revoke observation', b'Stop engine', b'Create verified backup',
                   b'Restore fresh workspace', b'Export selected files'):
        assert phrase not in bundle
    assert b'Project registration stays with your connected Codex plugin' in bundle
    entry = add_project(engine, tmp_path, 'ui')
    login(endpoint, client)
    payload = {'project_id': entry['project_id'], 'action': 'plan_create', 'arguments': {}}
    assert client.post('/studio/api/read', json=payload).json()['error'] == 'NOT_A_STUDIO_QUERY'
    assert client.get('/studio/assets-manifest.json').status_code == 404
    assert client.get('/studio/assets/../../endpoint.json').status_code == 404


def test_paged_plan_learning_and_lineage_reads_do_not_mutate_project(studio, tmp_path):
    engine, endpoint, client = studio
    entry = add_project(engine, tmp_path, 'paged')
    store = engine.directory.open(entry['project_id'], write=True)
    with WriterLease(store, engine.instance_id) as lease:
        PlanStore(store).create(PlanCreate(title='107 real rows', tasks=[TaskDefinition(task_id=f'T-{i}', title=f'Row {i}', requested_outcome='Review')
            for i in range(1, 108)]), lease, actor_id='fixture')
    login(endpoint, client)
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    response = client.post('/studio/api/read', json={'project_id': store.project_id, 'action': 'plan_read',
        'arguments': {'revision': 1, 'offset': 100, 'limit': 100}})
    assert response.status_code == 200
    assert len(response.json()['tasks']) == 7
    assert response.json()['tasks'][0]['position'] == 101
    for action in ('lineage_read', 'learning_read', 'memory_read', 'canon_read', 'continuation_read', 'linked_projects_read'):
        assert client.post('/studio/api/read', json={'project_id': store.project_id, 'action': action}).status_code == 200
    snapshot = client.get('/studio/api/snapshot', params={'project_id': store.project_id}, headers={'X-Studio-Read': '1'}).json()
    assert snapshot['project']['learning']['lessons'] == []
    assert snapshot['project']['lineage']['events'] == []
    assert snapshot['project']['memory']['locators'] == []
    assert snapshot['project']['linked_projects']['links'] == []
    assert {item['name'] for item in snapshot['actions'] if item['cross_project_read']} >= {'plan_read', 'lineage_read', 'memory_read'}
    assert snapshot['project']['canon']['exchanges'] == []
    assert snapshot['project']['continuity']['offers'] == []
    assert {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before


def test_owner_selects_two_projects_for_read_and_logout_cancels_authority(studio, tmp_path):
    engine, endpoint, client = studio
    entries = [add_project(engine, tmp_path, name) for name in ('first', 'second')]
    login(endpoint, client)
    before = [engine.directory.open(item['project_id']).database.read_bytes() for item in entries]
    payload = {'project_id': entries[0]['project_id'], 'action': 'cross_project_query', 'arguments': {
        'projects': [{'project_id': item['project_id'], 'queries': [{'action': 'plan_read'}]} for item in entries]}}
    response = client.post('/studio/api/read', json=payload)
    assert response.status_code == 200, response.text
    assert [item['project_id'] for item in response.json()['projects']] == [item['project_id'] for item in entries]
    assert [engine.directory.open(item['project_id']).database.read_bytes() for item in entries] == before
    client.post('/studio/api/logout', json={})
    denied = client.post('/studio/api/read', json=payload)
    assert not denied.is_success and denied.json()['error'] == 'STUDIO_AUTHENTICATION_REQUIRED'


def test_owner_previews_lane_records_without_exporting_files(studio, tmp_path):
    engine, endpoint, client = studio
    entry = add_project(engine, tmp_path, 'artifacts')
    store = engine.directory.open(entry['project_id'], write=True)
    with WriterLease(store, engine.instance_id) as lease:
        PlanStore(store).create(PlanCreate(title='Owner artifact export', tasks=[TaskDefinition(task_id='one',title='One task',requested_outcome='Export its view')]), lease, actor_id='fixture')
    login(endpoint, client)
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    payload = {'project_id':store.project_id,'action':'lane_view_preview','arguments':{'view_id':'plan.dependencies'}}
    preview = client.post('/studio/api/read', json=payload).json()
    assert preview['view_id'] == 'plan.dependencies'
    denied = client.post('/studio/api/view-export', json={})
    assert denied.status_code == 404 and denied.json()['error'] == 'UNKNOWN_ROUTE'
    assert {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before
    snapshot = client.get('/studio/api/snapshot', params={'project_id':store.project_id}, headers={'X-Studio-Read':'1'}).json()
    assert len(snapshot['lane_views']) == len(engine.registry.view_schemas()) == 20


def test_owner_inspects_recovery_without_creating_backup_or_restoring_source(studio, tmp_path):
    engine, endpoint, client = studio
    entry = add_project(engine, tmp_path, 'backup')
    store = engine.directory.open(entry['project_id'], write=True)
    login(endpoint, client)
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    for action in ('project_recovery_inspect','restoration_read'):
        assert client.post('/studio/api/read',json={'project_id':store.project_id,'action':action}).status_code == 200
    for route in ('backup', 'git-restore'):
        denied = client.post('/studio/api/' + route, json={})
        assert denied.status_code == 404 and denied.json()['error'] == 'UNKNOWN_ROUTE'
    assert {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before
    client.post('/studio/api/logout',json={})
    denied = client.post('/studio/api/backup',json={'project_id':store.project_id,'request':{'destination_root':str(tmp_path/'denied')}})
    assert not denied.is_success and denied.json()['error'] == 'STUDIO_AUTHENTICATION_REQUIRED'
    assert not (tmp_path/'denied').exists()
