import json
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.artifact_contract import ViewRefresh
from evidence_lane_plugin.canon_task_graph import CanonExpected, CanonPayload, CanonSend
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.project_memory import MemoryEdge, locator_identity
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from tests import test_canon_v4, test_code_profile_v4
from tests.test_canon_v4 import decide, send
from tests.test_code_profile_v4 import create_plan, execute
from tests.test_project_memory_v4 import ingest, source

pair = test_canon_v4.pair
system = test_canon_v4.system
code_system = test_code_profile_v4.code_system


@pytest.fixture
def views(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool((WorkerOperation('render_lane_view',
        'evidence_lane_plugin.artifact_contract','render_lane_view_worker',dependencies=('langgraph','langchain_core','graphviz')),),workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id, permissions=['read','write'])]))
        with engine.project_work.mutation(store) as lease:
            PlanStore(store).create(PlanCreate(title='Lane views', tasks=[TaskDefinition(task_id=name, title=name, requested_outcome='Verify lane semantics') for name in ('first','second','third')]), lease, actor_id=session.client_id)
        def call(action, arguments=None):
            return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id, arguments=arguments or {}), session)
        yield engine, store, call


def prepare(call, view='plan.dependencies', scope=None, **changes):
    preview = call('lane_view_preview', {'view_id': view, 'scope': scope or {}})
    assert preview.status == 'ok', preview.error
    value = preview.result
    request = ViewRefresh(view_id=view, scope=value['scope'], expected_generation=value['generation'],
        contract_digest=value['contract_digest'], source_digest=value['source_digest'], **changes)
    return value, request


def publish(call, request):
    result = call('lane_view_refresh', request.model_dump(mode='json'))
    assert result.status == 'ok', result.error
    return result.result


def test_plan_dependencies_select_only_requested_files_and_exact_replay(views):
    _, store, call = views
    before = store.database.read_bytes()
    preview, request = prepare(call)
    assert store.database.read_bytes() == before
    graph = preview['graph']
    nodes = {item['id']: item['key'] for item in graph['nodes']}
    assert {(nodes[item['source']], nodes[item['target']], item['kind']) for item in graph['edges']} == {('second','first','DEPENDS_ON'),('third','second','DEPENDS_ON')}
    source_state = PlanStore(store).snapshot().model_dump()
    result = publish(call, request)
    assert result['state'] == 'published' and result['generation'] == 1
    assert [Path(item['path']).name for item in result['files']] == ['plan.mmd']
    assert all(Path(item['path']).is_relative_to(store.root / 'plan') for item in result['files'])
    assert not any(Path(item['path']).suffix == '.dot' for item in result['files'])
    assert not list(store.root.rglob('plan-pointer.json'))
    assert len(store.lane_catalog()) == 9
    assert all((store.root / lane['database_path']).read_bytes().startswith(b'SQLite format 3') for lane in store.lane_catalog())
    assert publish(call, request) == result
    assert PlanStore(store).snapshot().model_dump() == source_state
    before = store.database.read_bytes()
    read = call('lane_view_read', {'view_id': request.view_id, 'include_content': True})
    assert read.result['state'] == 'fresh' and 'flowchart LR' in read.result['contents']['mmd']
    assert store.database.read_bytes() == before


def test_both_formats_share_semantics_and_pointer_is_snapshot_navigation(views):
    _, _, call = views
    preview, request = prepare(call, formats=['mmd','dot'], include_pointer=True)
    result = publish(call, request)
    assert {item['role'] for item in result['files']} == {'mmd','dot','pointer'}
    read = call('lane_view_read', {'view_id': request.view_id, 'include_content': True})
    assert read.status == 'ok', read.error
    receipts = read.result['manifest']['tool_evidence']
    assert read.result['manifest']['worker_execution']['worker_pid'] != __import__('os').getpid()
    assert receipts['mmd']['semantic_topology_sha256'] == receipts['dot']['semantic_topology_sha256']
    pointer = json.loads(read.result['contents']['pointer'])
    assert pointer['plan_revision'] == 1 and len(pointer['tasks']) == 3
    assert pointer['snapshot_binding']['source_digest'] == preview['source_digest']


def test_changed_source_rejects_old_preview_and_reports_stale_without_refresh(views):
    engine, store, call = views
    _, request = prepare(call)
    first = publish(call, request)
    _, stale = prepare(call)
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).transition('first', 'active', lease, expected_revision=1, actor_id='fixture')
    before = {str(path):path.read_bytes() for path in store.root.rglob('*.mmd')}
    assert call('lane_view_refresh', stale.model_dump()).error.code == 'VIEW_SOURCE_CHANGED'
    read = call('lane_view_read', {'view_id': request.view_id})
    assert read.result['state'] == 'stale' and read.result['refresh_performed'] is False
    assert {str(path):path.read_bytes() for path in store.root.rglob('*.mmd')} == before
    _, fresh = prepare(call)
    second = publish(call, fresh)
    assert second['generation'] == 2 and second['snapshot_digest'] != first['snapshot_digest']
    assert call('lane_view_read', {'view_id':request.view_id,'snapshot_digest':first['snapshot_digest']}).result['state'] == 'historical'


def test_pointer_only_empty_owner_and_unsupported_scope_do_not_invent_graphs(views):
    _, store, call = views
    _, request = prepare(call, formats=[], include_pointer=True)
    result = publish(call, request)
    assert [item['role'] for item in result['files']] == ['pointer']
    _, empty = prepare(call, view='chat_lineage.ancestry')
    assert publish(call, empty)['state'] == 'empty'
    assert not list((store.root / 'chat_lineage' / 'ancestry').rglob('*.mmd'))
    bad = empty.model_copy(update={'include_pointer': True})
    assert call('lane_view_refresh', bad.model_dump()).error.code == 'VIEW_FORMAT_UNSUPPORTED'
    assert call('lane_view_preview', {'view_id':'plan.dependencies','scope':{'query':'unsupported'}}).error.code == 'VIEW_SCOPE_UNSUPPORTED'


def test_file_tampering_and_missing_file_never_become_authority(views):
    _, store, call = views
    _, request = prepare(call)
    result = publish(call, request)
    path = Path(result['files'][0]['path'])
    before_plan = PlanStore(store).snapshot().model_dump()
    original = path.read_bytes()
    path.write_bytes(original.replace(b'first', b'FIRST'))
    assert call('lane_view_read', {'view_id':request.view_id}).error.code == 'VIEW_ARTIFACT_CHANGED'
    assert call('lane_view_refresh', request.model_dump()).error.code == 'VIEW_ARTIFACT_CHANGED'
    path.unlink()
    assert call('lane_view_read', {'view_id':request.view_id}).error.code == 'VIEW_ARTIFACT_MISSING'
    assert PlanStore(store).snapshot().model_dump() == before_plan


def test_receipt_failure_does_not_publish_staged_snapshot(views, monkeypatch):
    _engine, store, call = views
    _, request = prepare(call)
    first = publish(call, request)
    _, second = prepare(call, formats=['dot'])
    original = ProjectStore.append_receipt
    def fail(self, kind, *args, **kwargs):
        if kind == 'lane_view_published': raise RuntimeError('Injected publication failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ProjectStore, 'append_receipt', fail)
    failed = call('lane_view_refresh', second.model_dump())
    assert failed.error.code == 'TOOL_ADAPTER_FAILED'
    assert failed.error.details['tool_execution']['outcome'] == 'failed'
    read = call('lane_view_read', {'view_id':request.view_id})
    assert read.result['snapshot_digest'] == first['snapshot_digest']
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM views_snapshots').fetchone()[0] == 1
    assert list(store.root.rglob('*.dot'))  # unreferenced files are not current authority


def test_scope_bounds_are_disclosed_and_frozen_contract_rejects_stale_generation(views):
    engine, _, call = views
    preview, request = prepare(call, scope={'node_limit':1})
    assert len(preview['graph']['nodes']) == 1 and preview['graph']['truncated']
    publish(call, request)
    changed = request.model_copy(update={'request_id':str(uuid4())})
    assert call('lane_view_refresh', changed.model_dump()).error.code == 'VIEW_GENERATION_CONFLICT'
    changed = request.model_copy(update={'contract_digest':'0'*64})
    assert call('lane_view_refresh', changed.model_dump()).error.code == 'VIEW_CONTRACT_CHANGED'
    with pytest.raises(Exception) as error:
        engine.registry.register_view(engine.registry.get_view('plan.dependencies'))
    assert error.value.code == 'REGISTRY_FROZEN'


def test_lineage_ancestry_is_not_a_plan_dependency_graph(views):
    engine, store, call = views
    with engine.project_work.mutation(store) as lease:
        parent = ChatLineage(store).append(LineageRecord(kind='prompt',payload={'text':'Visible first'}),lease,client_id='fixture')
        ChatLineage(store).append(LineageRecord(kind='assistant',payload={'text':'Visible reply'},parent_event_id=parent.event_id),lease,client_id='fixture')
    preview, request = prepare(call, view='chat_lineage.ancestry')
    assert [edge['kind'] for edge in preview['graph']['edges']] == ['PARENT_OF']
    result = publish(call, request)
    assert Path(result['files'][0]['path']).name == 'chat_lineage.mmd'


def test_memory_preserves_typed_suppression_relationships(system):
    old, new = source(system, 'Old evidence'), source(system, 'New evidence')
    old_id, new_id = [locator_identity(system[1].project_id, item) for item in (old,new)]
    ingest(system, [old,new], [MemoryEdge(source_id=new_id,target_id=old_id,kind='SUPERSEDES',evidence=new.reference)])
    def call(action, arguments=None):
        return PublicActionSDKDispatcher(system[0]).execute(ActionRequest(action=action,project_id=system[1].project_id,arguments=arguments or {}),system[3])
    preview, request = prepare(call, view='memory.links', scope={'include_history':True}, include_pointer=True)
    assert [edge['kind'] for edge in preview['graph']['edges']] == ['SUPERSEDES']
    assert preview['graph']['edges'][0]['provenance'] == 'agent_report'
    assert preview['graph']['edges'][0]['evidence'] == new.reference.model_dump()
    assert all(node['locator']['source_client_id'] == system[3].client_id for node in preview['graph']['nodes'])
    assert {node['state'] for node in preview['graph']['nodes']} == {'suppressed','registered_content_reference'}
    assert publish(call, request)['state'] == 'published'


def test_learning_graph_comes_from_actual_verified_exit(code_system):
    create_plan(code_system)
    execute(code_system)
    def call(action, arguments=None):
        return PublicActionSDKDispatcher(code_system[0]).execute(ActionRequest(action=action,project_id=code_system[1].project_id,arguments=arguments or {}),code_system[2])
    preview, request = prepare(call, view='learning.provenance')
    assert {node['kind'] for node in preview['graph']['nodes']} == {'learning_version','verified_exit'}
    assert [edge['kind'] for edge in preview['graph']['edges']] == ['VERIFIED_SOURCE_OF']
    assert Path(publish(call, request)['files'][0]['path']).name == 'agent-learning.mmd'


def test_canon_view_tracks_receiver_decision_and_preserves_source_authorities(pair):
    invoke,sender,_receiver,_source_id,_target_id,_system = pair
    sent,_ = send(pair)
    def call(action, arguments=None): return invoke(sender, action, arguments or {})
    preview, request = prepare(call, view='canon.consequences', include_pointer=True)
    assert 'RECEIVER_RECEIVED' in {edge['kind'] for edge in preview['graph']['edges']}
    publish(call, request)
    decide(pair, sent)
    assert call('lane_view_read', {'view_id':request.view_id}).result['state'] == 'stale'
    preview, fresh = prepare(call, view='canon.consequences', include_pointer=True)
    assert 'RECEIVER_ADMITTED' in {edge['kind'] for edge in preview['graph']['edges']}
    publish(call, fresh)
    assert call('lane_view_read', {'view_id':request.view_id}).result['state'] == 'fresh'


def test_canon_contract_and_reply_relationships_are_kept_distinct(pair):
    invoke,sender,receiver,source_id,target_id,_ = pair
    expected = invoke(receiver, 'task_evidence_expect', CanonExpected(receiver_id=target_id, contract_key='review', sender_ids=[source_id], kinds=['requirements']))
    assert expected.status == 'ok', expected.error
    first,_ = send(pair, expected_contract=expected.result['contract_digest'])
    decide(pair, first, 'clarify')
    reply = invoke(receiver, 'task_evidence_send', CanonSend(sender_id=target_id,receiver_id=source_id,kind='clarification',
        reply_to=first.result['exchange_id'],payload=CanonPayload(summary='Clarify the bounded requirement')))
    assert reply.status == 'ok', reply.error
    def call(action, arguments=None):return invoke(sender, action, arguments or {})
    preview, request = prepare(call, view='canon.consequences', include_pointer=True)
    kinds = {edge['kind'] for edge in preview['graph']['edges']}
    assert {'EXPECTS','ADDRESSES_CONTRACT','REPLIES_TO'} <= kinds
    assert publish(call, request)['state'] == 'published'


def test_dot_labels_are_literal_and_lane_contracts_reject_other_relationships(views):
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.graph_pipeline import SemanticGraph
    from evidence_lane_plugin.lane_traversal import validate_view_graph
    semantic = SemanticGraph('literal')
    semantic.add_node('a', '<IMG SRC="private-file.png">')
    dot, _ = semantic.render_dot()
    assert 'label="<IMG SRC=' in dot and 'label=<' not in dot
    engine, store, call = views
    preview, _ = prepare(call)
    graph = preview['graph']
    graph['edges'][0]['kind'] = 'SUPERSEDES'
    with pytest.raises(LaneError) as error:
        validate_view_graph(store.project_id, engine.registry.get_view('plan.dependencies').schema(), preview['scope'], graph)
    assert error.value.code == 'VIEW_TOPOLOGY_CONTRACT'


def test_wrong_worker_input_does_not_publish_or_change_source(views, monkeypatch):
    from concurrent.futures import Future
    engine, store, call = views
    _, request = prepare(call)
    before = PlanStore(store).snapshot().model_dump()
    def wrong(*args, **kwargs):
        future = Future()
        future.set_result({'status':'ok','result':{'input_digest':'0'*64},'worker_pid':999,'loaded_modules':[]})
        return future
    monkeypatch.setattr(engine.workers, 'submit', wrong)
    result = call('lane_view_refresh', request.model_dump())
    assert result.error.code == 'VIEW_WORKER_BINDING'
    assert not list((store.root / 'plan' / 'dependencies').rglob('*.mmd'))
    assert PlanStore(store).snapshot().model_dump() == before


def test_universe_topology_exports_separately_and_registers_natural_artifact_hashes(views):
    _, store, call = views
    preview, request = prepare(call, view='universe.topology', formats=['mmd', 'dot'])
    assert {'project', 'lane', 'plan_task'} <= {node['kind'] for node in preview['graph']['nodes']}
    assert 'root_pv' not in {node['kind'] for node in preview['graph']['nodes']}
    published = publish(call, request)
    assert {Path(row['path']).name for row in published['files']} == {'project_universe.mmd', 'project_universe.dot'}
    read = call('lane_view_read', {'view_id': request.view_id})
    assert read.status == 'ok' and read.result['state'] == 'fresh', read.error
    with store.lane('universe').connection(read_only=True) as connection:
        assert connection.execute('SELECT view_id FROM views_current').fetchone()[0] == 'universe.topology'
    with store.connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='views_current'").fetchone()
    inspected = call('project_evidence_map_inspect')
    assert inspected.status == 'ok', inspected.error
    universe = next(row for row in inspected.result['lanes'] if row['lane_id'] == 'universe')
    assert universe['artifact_references'][0]['snapshot_digest'] == published['snapshot_digest']
    assert {row['role'] for row in universe['artifact_references'][0]['files']} == {'mmd', 'dot'}
    Path(published['files'][0]['path']).write_text('changed derived graph')
    assert call('project_evidence_map_inspect').error.code == 'UNIVERSE_VIEW_INTEGRITY'


def test_universe_topology_becomes_stale_after_plan_change_without_auto_refresh(views):
    engine, store, call = views
    _, request = prepare(call, view='universe.topology')
    published = publish(call, request)
    files = {row['path']: Path(row['path']).read_bytes() for row in published['files']}
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).transition('first', 'active', lease, expected_revision=1, actor_id='fixture')
    result = call('lane_view_read', {'view_id': request.view_id})
    assert result.result['state'] == 'stale'
    assert all(Path(path).read_bytes() == content for path, content in files.items())
