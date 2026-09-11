"""Original task-edge semantics through the current engine and separate lanes."""
# ruff: noqa: F811 -- pytest resolves imported fixtures by name.
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from evidence_lane_plugin.canon_task_graph import (
    CanonExpected,
    CanonJoin,
    CanonPayload,
    CanonSend,
    CanonStore,
    CanonTaskEdgeRegister,
    CanonTaskGraphRead,
)
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.plan_runtime import content_digest
from evidence_lane_plugin.recovery_snapshot import inventory, relative_file
from evidence_lane_plugin.storage import project_snapshot

from tests.test_canon_v4 import pair, send  # noqa: F401
from tests.test_session_v4 import call
from tests.test_session_v4 import selected as system  # noqa: F401


def register(pair, **changes):
    invoke, sender, _, source, target, (_, project, _, _) = pair
    request = CanonTaskEdgeRegister.model_validate({'source_id':source,
        'destination':{'project_id':project.project_id,'participant_id':target},
        'contract_digest':'a'*64, 'schema_digest':content_digest(CanonPayload.model_json_schema()),
        'expected_return_contract':'b'*64, **changes})
    result = invoke(sender, 'canon_task_edge_register', request)
    assert result.status == 'ok', result
    return result, request


def bind(pair, edge):
    invoke, _, receiver, _, _, (_, project, _, _) = pair
    request = {'source_project_id':project.project_id, 'edge_id':edge.result['edge_id'],
        'edge_digest':edge.result['edge_digest']}
    result = invoke(receiver, 'canon_task_edge_bind', request)
    assert result.status == 'ok', result
    return result


def test_edge_registration_binding_replay_and_named_lane_file(pair):
    invoke, sender, receiver, _, _, (_, project, _, _) = pair
    edge, request = register(pair, permitted_actions=['code_query'], permitted_paths=['src'], permitted_tools=['ripgrep'])
    value = edge.result
    assert value['local_role'] == 'source' and not value['destination_bound']
    assert not value['host_task_created'] and not value['source_write_granted'] and not value['plan_mutated']
    path = project.root / value['artifact_path']
    assert path.name == value['edge_id'] + '.json' and 'authorities/canon/files/graph/' in value['artifact_path']
    assert json.loads(path.read_bytes()) == value['edge'] and content_digest(value['edge']) == value['edge_digest']
    assert invoke(sender, 'canon_task_edge_register', request).result['duplicate']
    wrong = invoke(sender, 'canon_task_edge_bind', {'source_project_id':project.project_id,
        'edge_id':value['edge_id'],'edge_digest':value['edge_digest']})
    assert wrong.error.code == 'CANON_OWNER_MISMATCH'
    bound = bind(pair, edge)
    assert bound.result['local_role'] == 'both' and bound.result['destination_bound']
    assert bind(pair, edge).result['duplicate']
    changed = request.model_copy(update={'request_id':str(uuid4()), 'contract_digest':'c'*64})
    assert invoke(sender, 'canon_task_edge_register', changed).error.code == 'CANON_EDGE_IMMUTABILITY_CONFLICT'
    before = project.pv_head(), project.lane_catalog()
    graph = invoke(receiver, 'canon_graph', {})
    assert graph.status == 'ok', graph
    assert len(graph.result['edges']) == 1 and len(graph.result['missing_returns']) == 1
    assert graph.result['graph_digest'] == content_digest({key:value for key,value in graph.result.items() if key != 'graph_digest'})
    assert not graph.result['cross_project_graph_complete'] and graph.result['native_task_attestation'] == 'not_provided'
    inspected = invoke(receiver, 'canon_inspect', {})
    assert inspected.result['objects_verified']['task_edges'] == inspected.result['objects_verified']['task_edge_bindings'] == 1
    assert project.pv_head() == before[0] and project.lane_catalog() == before[1]


def test_task_graph_keeps_diamond_fan_in_fan_out_and_rejects_cycles(pair):
    invoke, sender, _, source, target, (_, project, _, _) = pair
    third = invoke(sender, 'canon_join', CanonJoin(label='Third engine participant')).result['participant_id']
    fourth = invoke(sender, 'canon_join', CanonJoin(label='Fourth engine participant')).result['participant_id']
    register(pair)
    register(pair, destination={'project_id':project.project_id,'participant_id':third})
    register(pair, source_id=third, destination={'project_id':project.project_id,'participant_id':fourth})
    # The receiver owns the other branch of the diamond.
    receiver_edge = CanonTaskEdgeRegister(source_id=target, destination={'project_id':project.project_id,'participant_id':fourth},
        contract_digest='a'*64, schema_digest='b'*64, expected_return_contract='c'*64)
    assert invoke(pair[2], 'canon_task_edge_register', receiver_edge).status == 'ok'
    assert len(invoke(sender, 'canon_graph', {}).result['edges']) == 4
    before = CanonStore(project).task_graph(CanonTaskGraphRead()).model_dump()
    cycle = receiver_edge.model_copy(update={'request_id':str(uuid4()),'edge_id':str(uuid4()),'source_id':fourth,
        'destination':receiver_edge.destination.model_copy(update={'participant_id':source})})
    assert invoke(sender, 'canon_task_edge_register', cycle).error.code == 'CANON_TASK_GRAPH_CYCLE_BLOCKED'
    self_cycle = cycle.model_copy(update={'request_id':str(uuid4()),'edge_id':str(uuid4()),'source_id':source})
    assert invoke(sender, 'canon_task_edge_register', self_cycle).error.code == 'CANON_TASK_GRAPH_CYCLE_BLOCKED'
    # The SDK may append a failed-action receipt; rejected edges must leave
    # the actual Canon graph and Canon event history unchanged.
    assert CanonStore(project).task_graph(CanonTaskGraphRead()).model_dump() == before


def test_edge_returns_require_bound_exact_input_and_current_receiver_admission(pair):
    invoke, sender, receiver, source, target, _ = pair
    expected = invoke(receiver, 'canon_expect', CanonExpected(receiver_id=target, contract_key='edge_input',
        sender_ids=[source], kinds=['requirements'], auto_admit=True)).result['contract_digest']
    returning = invoke(sender, 'canon_expect', CanonExpected(receiver_id=source, contract_key='edge_result',
        sender_ids=[target], kinds=['result'], fields={'count':'integer'})).result['contract_digest']
    edge, _ = register(pair, contract_digest=expected, expected_return_contract=returning)
    request = CanonSend(sender_id=source, receiver_id=target, kind='requirements', payload={'summary':'Inspect the selected source'},
        expected_contract=expected, return_contract=returning, edge_id=edge.result['edge_id'])
    assert invoke(sender, 'canon_send', request).error.code == 'CANON_EDGE_NOT_BOUND'
    classified = invoke(sender, 'canon_classify', request)
    assert classified.status == 'ok' and classified.result['reasons'] == ['CANON_EDGE_NOT_BOUND']
    bind(pair, edge)
    original = invoke(sender, 'canon_send', request)
    assert original.status == 'ok' and original.result['state'] == 'admitted', original
    result = CanonSend(sender_id=target, receiver_id=source, kind='result', reply_to=original.result['exchange_id'],
        expected_contract=returning, edge_id=edge.result['edge_id'], payload={'summary':'One exact result','fields':{'count':1}})
    pending = invoke(receiver, 'canon_send', result)
    assert pending.status == 'ok' and pending.result['state'] == 'received', pending
    assert len(invoke(sender, 'canon_graph', {}).result['missing_returns']) == 1
    admitted = invoke(sender, 'canon_decide', {'exchange_id':pending.result['exchange_id'],
        'envelope_digest':pending.result['envelope_digest'],'expected_version':1,'decision':'admit','reason':'Verified this typed edge return.'})
    assert admitted.status == 'ok', admitted
    graph = invoke(sender, 'canon_graph', {})
    assert graph.result['missing_returns'] == [] and graph.result['edges'][0]['local_admitted_returns'][0]['exchange_id'] == pending.result['exchange_id']
    # A later contract replacement must not erase the historical admitted return.
    updated = invoke(sender, 'canon_expect', CanonExpected(receiver_id=source, contract_key='edge_result', expected_version=1,
        sender_ids=[target], kinds=['result'], fields={'count':'integer'}, active=False))
    assert updated.status == 'ok' and invoke(sender, 'canon_graph', {}).result['missing_returns'] == []
    stale = invoke(receiver, 'canon_send', result.model_copy(update={'request_id':str(uuid4())}))
    assert stale.result['state'] == 'received' and stale.result['compatibility_reasons'] == ['CANON_CONTRACT_MISMATCH']


def test_result_for_another_edge_does_not_close_a_missing_return(pair):
    invoke, sender, receiver, source, target, (_, project, _, _) = pair
    expected = invoke(receiver, 'canon_expect', CanonExpected(receiver_id=target, contract_key='edge_input',
        sender_ids=[source], kinds=['requirements'], auto_admit=True)).result['contract_digest']
    returning = invoke(sender, 'canon_expect', CanonExpected(receiver_id=source, contract_key='edge_return',
        sender_ids=[target], kinds=['result'], auto_admit=True)).result['contract_digest']
    first, _ = register(pair, contract_digest=expected, expected_return_contract=returning)
    second, _ = register(pair, contract_digest=expected, expected_return_contract=returning)
    bind(pair, first)
    bind(pair, second)
    original, _ = send(pair, edge_id=first.result['edge_id'], expected_contract=expected, return_contract=returning)
    wrong = CanonSend(sender_id=target, receiver_id=source, kind='result', expected_contract=returning,
        edge_id=second.result['edge_id'], reply_to=original.result['exchange_id'], payload={'summary':'Wrong edge'})
    assert invoke(receiver, 'canon_send', wrong).error.code == 'CANON_EDGE_RETURN_MISMATCH'
    assert len(CanonStore(project).task_graph(CanonTaskGraphRead()).missing_returns) == 2


def test_graph_budgets_dependencies_expiry_and_file_tamper_fail_closed(pair):
    invoke, sender, _, _, _, (_, project, _, _) = pair
    edge, request = register(pair)
    assert invoke(sender, 'canon_graph', {'record_limit':1}).error.code == 'CANON_HISTORY_BUDGET'
    nonexistent = request.model_copy(update={'request_id':str(uuid4()),'edge_id':str(uuid4()),'dependency_ids':[str(uuid4())]})
    assert invoke(sender, 'canon_task_edge_register', nonexistent).error.code == 'CANON_EDGE_DEPENDENCY_NOT_FOUND'
    expired = request.model_copy(update={'request_id':str(uuid4()),'edge_id':str(uuid4()),
        'expires_at':(datetime.now(UTC)-timedelta(seconds=1)).isoformat()})
    assert invoke(sender, 'canon_task_edge_register', expired).error.code == 'CANON_EXCHANGE_EXPIRED'
    path = project.root / edge.result['artifact_path']
    path.write_bytes(b'{}')
    assert invoke(sender, 'canon_graph', {}).error.code == 'CANON_EDGE_FILE_INTEGRITY'
    assert invoke(sender, 'canon_inspect', {}).error.code == 'CANON_EDGE_FILE_INTEGRITY'


def test_named_edges_enter_coherent_recovery_inventory_only_after_registration(pair):
    _, _, _, _, _, (_, project, _, _) = pair
    edge, _ = register(pair)
    orphan = project.lane('canon').files / 'graph' / ('0'*64) / (str(uuid4())+'.json')
    orphan.parent.mkdir(parents=True)
    orphan.write_text('{}')
    with project_snapshot(project.root):
        files = inventory(project, require_quiescent=False)['files']
    selected = [item for item in files if item['path'] == edge.result['artifact_path']]
    assert len(selected) == 1 and selected[0]['sha256'] == edge.result['edge_digest']
    assert not any(item['path'] == orphan.relative_to(project.root).as_posix() for item in files)
    assert relative_file(selected[0]['path']).as_posix() == selected[0]['path']


def test_cross_project_edge_binding_verifies_source_without_mutating_it(system, tmp_path):
    engine, source_project, _, unselected = system
    source_root = tmp_path / 'second-source'
    source_root.mkdir()
    (source_root/'input.txt').write_text('Second original source')
    entry = engine.directory.register(tmp_path/'second-state', source_root=source_root, create=True, read_only=False)
    destination_project = engine.directory.open(entry['project_id'], write=True)
    selections = [ProjectSelection(project_id=source_project.project_id,permissions=['read','write']),
        ProjectSelection(project_id=destination_project.project_id,permissions=['read','write'])]
    _, source = engine.clients.connect(ConnectRequest(projects=selections))
    _, destination = engine.clients.connect(ConnectRequest(projects=selections))
    a = call(engine, source_project, source, 'canon_join', {'label':'Source'}).result['participant_id']
    b = call(engine, destination_project, destination, 'canon_join', {'label':'Destination'}).result['participant_id']
    c = call(engine, source_project, source, 'canon_join', {'label':'Source-local dependency'}).result['participant_id']
    dependency = call(engine, source_project, source, 'canon_task_edge_register', {
        'source_id':a,'destination':{'project_id':source_project.project_id,'participant_id':c},
        'contract_digest':'a'*64,'schema_digest':'b'*64,'expected_return_contract':'c'*64})
    assert dependency.status == 'ok', dependency
    request = CanonTaskEdgeRegister(source_id=a, destination={'project_id':destination_project.project_id,'participant_id':b},
        contract_digest='a'*64, schema_digest=content_digest(CanonPayload.model_json_schema()), expected_return_contract='c'*64,
        dependency_ids=[dependency.result['edge_id']])
    before_destination = destination_project.pv_head(), destination_project.lane_catalog()
    edge = call(engine, source_project, source, 'canon_task_edge_register', request.model_dump())
    assert edge.status == 'ok', edge
    assert (destination_project.pv_head(), destination_project.lane_catalog()) == before_destination
    bind_request = {'source_project_id':source_project.project_id, 'edge_id':edge.result['edge_id'],'edge_digest':edge.result['edge_digest']}
    before_source = source_project.pv_head(), source_project.lane_catalog()
    bound = call(engine, destination_project, destination, 'canon_task_edge_bind', bind_request)
    assert bound.status == 'ok', bound
    assert bound.result['local_role'] == 'destination' and bound.result['edge'] == edge.result['edge']
    assert (source_project.pv_head(), source_project.lane_catalog()) == before_source
    graph = CanonStore(destination_project).task_graph(CanonTaskGraphRead())
    assert graph.missing_returns[0]['state'] == 'source_return_state_not_read'
    assert graph.edges[0]['dependencies_not_recorded_locally'] == [dependency.result['edge_id']]
    # A client without the foreign project selection cannot inspect that source.
    _, destination_only = engine.clients.connect(ConnectRequest(projects=[selections[1]]))
    denied = call(engine, destination_project, destination_only, 'canon_task_edge_bind', bind_request)
    assert denied.status == 'error' and denied.error.code in {'PROJECT_NOT_SELECTED','PROJECT_PERMISSION_DENIED'}
    wrong = call(engine, destination_project, destination, 'canon_task_edge_bind', {**bind_request,'edge_digest':'0'*64})
    assert wrong.error.code == 'CANON_EDGE_SOURCE_MISMATCH'
    unselected_request = request.model_copy(update={'request_id':str(uuid4()),'edge_id':str(uuid4())})
    denied = call(engine, source_project, unselected, 'canon_task_edge_register', unselected_request.model_dump())
    assert denied.status == 'error' and denied.error.code in {'PROJECT_NOT_SELECTED','PROJECT_PERMISSION_DENIED'}


def test_empty_graph_read_does_not_initialize_canon(system):
    engine, project, _, client = system
    before = project.pv_head(), project.lane_catalog()
    result = call(engine, project, client, 'canon_graph')
    assert result.status == 'ok' and result.result['edges'] == [], result
    assert (project.pv_head(), project.lane_catalog()) == before


def test_aborted_graph_write_keeps_orphan_unregistered_and_retry_is_safe(pair, monkeypatch):
    invoke, sender, _, source, target, (_, project, _, _) = pair
    original = CanonStore._event
    def fail_after_file(connection, request_id, kind, *args):
        if kind == 'task_edge_register':
            raise RuntimeError('Injected failure after the named file was written')
        return original(connection, request_id, kind, *args)
    monkeypatch.setattr(CanonStore, '_event', staticmethod(fail_after_file))
    request = CanonTaskEdgeRegister(source_id=source, destination={'project_id':project.project_id,'participant_id':target},
        contract_digest='a'*64, schema_digest='b'*64, expected_return_contract='c'*64)
    before = CanonStore(project).verify_history()
    failed = invoke(sender, 'canon_task_edge_register', request)
    assert failed.status == 'error'
    assert CanonStore(project).task_graph(CanonTaskGraphRead()).edges == []
    assert CanonStore(project).verify_history() == before
    assert len(list((project.lane('canon').files/'graph').glob('*/*.json'))) == 1
    monkeypatch.setattr(CanonStore, '_event', staticmethod(original))
    retried = invoke(sender, 'canon_task_edge_register', request)
    assert retried.status == 'ok', retried
    assert len(CanonStore(project).task_graph(CanonTaskGraphRead()).edges) == 1
    with project_snapshot(project.root):
        files = inventory(project, require_quiescent=False)['files']
    assert len([item for item in files if '/files/graph/' in item['path']]) == 1


def test_graph_actions_through_actual_mcp_stdio_and_persistent_backend(system):
    import asyncio
    import sys
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine, project, _, _ = system
    plugin = Path(__file__).resolve().parents[1]/'plugins/evidence-lane-plugin'
    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m','evidence_lane_plugin.mcp_adapter','--runtime-root',str(engine.root),
                  '--project-id',project.project_id,'--permission','read','--permission','write'], env={'PYTHONPATH':str(plugin/'src')})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
            await session.initialize()
            async def invoke(action, arguments):
                response = (await session.call_tool(action, {'project_id':project.project_id,'arguments':arguments})).structuredContent
                assert response and response['status'] == 'ok', json.dumps(response)
                assert response['tool_execution']['env_uop']['action_name'] == action
                assert response['tool_execution']['native_host_tool_attested'] is False
                return response['result']
            source = await invoke('canon_join', {'label':'MCP source engine participant'})
            destination = await invoke('canon_join', {'label':'MCP destination engine participant'})
            edge = await invoke('canon_task_edge_register', {'source_id':source['participant_id'],
                'destination':{'project_id':project.project_id,'participant_id':destination['participant_id']},
                'contract_digest':'a'*64, 'schema_digest':'b'*64, 'expected_return_contract':'c'*64})
            bound = await invoke('canon_task_edge_bind', {'source_project_id':project.project_id,
                'edge_id':edge['edge_id'],'edge_digest':edge['edge_digest']})
            assert bound['destination_bound'] and bound['native_task_attestation'] == 'not_provided'
            before = project.pv_head()
            graph = await invoke('canon_graph', {})
            assert graph['edges'][0]['edge_digest'] == edge['edge_digest'] and project.pv_head() == before
    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(exercise())
