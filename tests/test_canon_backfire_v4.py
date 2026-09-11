"""Conditional Canon backfire and semantic result replay in the original owner."""
# ruff: noqa: F811 -- pytest resolves imported fixture names.
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import CanonPayload, CanonStore
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.plan_runtime import content_digest

from tests.test_canon_transport_v4 import projects, snapshot, successful  # noqa: F401
from tests.test_session_v4 import call
from tests.test_session_v4 import selected as system  # noqa: F401


@pytest.fixture
def three(system, tmp_path):
    engine, first, _, _ = system
    stores = [first]
    for name in ('worker','upstream'):
        source = tmp_path/f'{name}-source'
        source.mkdir()
        (source/'input.txt').write_text(name)
        record = engine.directory.register(tmp_path/f'{name}-state',source_root=source,create=True,read_only=False)
        stores.append(engine.directory.open(record['project_id'],write=True))
    selections = [ProjectSelection(project_id=store.project_id,permissions=['read','write']) for store in stores]
    clients = [engine.clients.connect(ConnectRequest(projects=selections))[1] for _ in stores]
    participants = [successful(engine,store,client,'canon_join',{'label':f'Participant {i}'})['participant_id']
        for i,(store,client) in enumerate(zip(stores,clients,strict=True))]
    return engine, stores, clients, participants


def endpoint(store, participant):
    return {'project_id':store.project_id,'participant_id':participant}


def prepare(three):
    engine, (a,b,c), (ca,cb,cc), (pa,pb,pc) = three
    expectation = successful(engine,b,cb,'canon_expect',{'receiver_id':pb,'contract_key':'input',
        'sender_endpoints':[endpoint(a,pa)],'kinds':['requirements'],'auto_admit':True})
    original = successful(engine,a,ca,'canon_send',{'sender_id':pa,'receiver_id':pb,'destination_project_id':b.project_id,
        'kind':'requirements','expected_contract':expectation['contract_digest'],'payload':{'summary':'Inspect an exact source input'}})
    incoming = successful(engine,b,cb,'canon_receive',{'source_project_id':a.project_id,
        'exchange_id':original['exchange_id'],'envelope_digest':original['envelope_digest']})
    recipient_contract = successful(engine,c,cc,'canon_expect',{'receiver_id':pc,'contract_key':'backfire',
        'sender_endpoints':[endpoint(b,pb)],'kinds':['backfire'],'auto_admit':True})
    return_contract = successful(engine,a,ca,'canon_expect',{'receiver_id':pa,'contract_key':'backfire_return',
        'sender_endpoints':[endpoint(c,pc)],'kinds':['result'],'auto_admit':True})
    arguments = {'request_id':str(uuid4()),'admitted_exchange_id':incoming['exchange_id'],
        'admitted_envelope_digest':incoming['envelope_digest'],'failure_class':'MISSING_INFORMATION_FROM_SOURCE',
        'recipient':endpoint(c,pc),'requested_contract':recipient_contract['contract_digest'],'requested_revision':2,
        'payload':{'summary':'The selected upstream owner must supply the missing source information'},
        'dependency_ids':[incoming['exchange_id']],'return_route':endpoint(a,pa),'return_contract':return_contract['contract_digest'],
        'expires_at':(datetime.now(UTC)+timedelta(hours=1)).isoformat()}
    return original, arguments


def test_exact_third_party_backfire_and_declared_return_remain_receiver_owned(three):
    from evidence_lane_plugin.canon_consequence_graph import canon_view
    from evidence_lane_plugin.lane_contract import ViewScope

    engine, (a,b,c), (ca,cb,cc), (pa,pb,pc) = three
    _, args = prepare(three)
    before = snapshot(a),snapshot(c)
    raised = successful(engine,b,cb,'canon_backfire',args)
    assert raised['state'] == 'sealed' and not raised['automatic_retry_allowed']
    assert (snapshot(a),snapshot(c)) == before
    body = json.loads((b.root/raised['artifact_path']).read_bytes())
    details = body['message']['backfire']
    assert details['trace'] == [endpoint(b,pb),endpoint(c,pc)] and details['return_route'] == endpoint(a,pa)
    assert body['message']['reply_to'] is None and body['revision'] == 1
    assert not body['source_write_granted'] and not body['plan_mutated']
    backfire_locator = {'source_project_id':b.project_id,'exchange_id':raised['exchange_id'],'envelope_digest':raised['envelope_digest']}
    peer_before = snapshot(b),snapshot(a)
    preview = successful(engine,c,cc,'canon_packet_classify',backfire_locator)
    assert preview['classification'] == 'expected' and preview['automatic_admission_permitted']
    assert (snapshot(b),snapshot(a)) == peer_before
    received = successful(engine,c,cc,'canon_receive',backfire_locator)
    assert received['state'] == 'admitted' and (snapshot(b),snapshot(a)) == peer_before
    inbox = successful(engine,c,cc,'canon_inbox',{'exchange_id':received['exchange_id']})
    assert inbox['packets'][0]['backfire'] == details
    # C returns to the explicitly declared A, which never received B's packet.
    returned = successful(engine,c,cc,'canon_send',{'sender_id':pc,'receiver_id':pa,'destination_project_id':a.project_id,
        'kind':'result','reply_to':received['exchange_id'],'expected_contract':args['return_contract'],
        'payload':{'summary':'The upstream source information is available'}})
    before_c = snapshot(c)
    admitted = successful(engine,a,ca,'canon_receive',{'source_project_id':c.project_id,
        'exchange_id':returned['exchange_id'],'envelope_digest':returned['envelope_digest']})
    assert admitted['state'] == 'admitted' and snapshot(c) == before_c
    with a.lane('canon').connection(read_only=True) as db:
        assert not db.execute('SELECT 1 FROM canon_exchanges WHERE exchange_id=?',(raised['exchange_id'],)).fetchone()
        assert db.execute('SELECT parent_id FROM canon_exchanges WHERE exchange_id=?',(returned['exchange_id'],)).fetchone()[0] is None
    assert successful(engine,b,cb,'canon_inspect')['objects_verified']['conditional_operations'] == 1
    assert successful(engine,a,ca,'canon_inspect')['foreign_key_errors'] == []
    graph = canon_view(c,ViewScope())
    assert {'REQUESTS_REVISION','DECLARES_RETURN_ROUTE','CORRECTION_TRACE','REPLIES_TO'} <= {edge['kind'] for edge in graph['edges']}
    reference = next(node for node in graph['nodes'] if node['kind'] == 'canon_exchange_reference')
    assert reference['locator']['record_project_id'] == b.project_id and not reference['locator']['receiver_state_observed']
    contract = engine.registry.get_view('canon.consequences')
    assert {node['kind'] for node in graph['nodes']} <= set(contract.node_kinds)
    assert {edge['kind'] for edge in graph['edges']} <= set(contract.edge_kinds)


def test_backfire_dedup_preserves_original_bytes_and_reserves_replay_request_ids(three):
    engine, (_,b,_), (_,cb,_), _ = three
    _, args = prepare(three)
    first = successful(engine,b,cb,'canon_backfire',args)
    assert not first['duplicate']
    alias = {**args,'request_id':str(uuid4()),'expires_at':(datetime.now(UTC)+timedelta(hours=2)).isoformat()}
    repeated = successful(engine,b,cb,'canon_backfire',alias)
    assert repeated['duplicate'] and repeated['envelope_digest'] == first['envelope_digest']
    assert repeated['exchange_id'] == first['exchange_id'] and repeated['dedup_key'] == first['dedup_key']
    assert successful(engine,b,cb,'canon_backfire',alias)['duplicate']
    assert json.loads((b.root/first['artifact_path']).read_bytes())['message']['expires_at'] == args['expires_at']
    conflict = {**args,'request_id':str(uuid4()),'payload':{'summary':'Different evidence under the same conditional request'}}
    assert call(engine,b,cb,'canon_backfire',conflict).error.code == 'CANON_BACKFIRE_DEDUP_CONFLICT'
    assert call(engine,b,cb,'canon_backfire',{**conflict,'request_id':alias['request_id']}).error.code == 'CANON_REQUEST_CONFLICT'
    with b.lane('canon').connection(read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM canon_events WHERE kind='backfire'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM canon_events WHERE kind='backfire_replay'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM canon_exchanges WHERE kind='backfire'").fetchone()[0] == 1
    assert CanonStore(b).verify_history()['events_verified'] > 0


@pytest.mark.parametrize('failure', ['EXECUTION_FAILURE_REQUIRES_UPSTREAM_ACTION',
    'MISSING_INFORMATION_FROM_SOURCE','NEW_REQUIREMENT_FROM_SOURCE','LINKED_TASK_INPUT_REQUIRED'])
def test_only_the_admitted_input_owner_can_raise_each_retained_backfire_class(three, failure):
    engine, (_,b,_), (ca,cb,_), _ = three
    _, args = prepare(three)
    args['failure_class'] = failure
    assert call(engine,b,ca,'canon_backfire',args).error.code == 'CANON_OWNER_MISMATCH'
    assert successful(engine,b,cb,'canon_backfire',args)['state'] == 'sealed'


def test_backfire_requires_current_admission_revision_exact_digest_and_acyclic_trace(three):
    engine, (a,b,c), (ca,cb,cc), (pa,pb,pc) = three
    original, args = prepare(three)
    assert call(engine,b,cb,'canon_backfire',{**args,'admitted_envelope_digest':'0'*64}).error.code == 'CANON_BACKFIRE_INPUT_MISMATCH'
    assert call(engine,b,cb,'canon_backfire',{**args,'trace':[endpoint(c,pc)]}).error.code == 'CANON_BACKFIRE_ROUTE_CYCLE_BLOCKED'
    assert call(engine,b,cb,'canon_backfire',{**args,'trace':[endpoint(b,pb)]}).error.code == 'CANON_BACKFIRE_ROUTE_CYCLE_BLOCKED'
    assert call(engine,b,cb,'canon_backfire',{**args,'dependency_ids':[str(uuid4())]}).error.code == 'CANON_EDGE_NOT_FOUND'
    proposed = successful(engine,b,cb,'canon_backfire',args)
    # Admission of a correction supersedes the old input and increments its
    # immutable packet revision; mutable state-version numbers are not revisions.
    correction = successful(engine,a,ca,'canon_send',{'sender_id':pa,'receiver_id':pb,'destination_project_id':b.project_id,
        'kind':'correction','supersedes':original['exchange_id'],'payload':{'summary':'A corrected exact source input'}})
    pending = successful(engine,b,cb,'canon_receive',{'source_project_id':a.project_id,
        'exchange_id':correction['exchange_id'],'envelope_digest':correction['envelope_digest']})
    updated = {**args,'request_id':str(uuid4()),'admitted_exchange_id':pending['exchange_id'],'admitted_envelope_digest':pending['envelope_digest']}
    assert call(engine,b,cb,'canon_backfire',updated).error.code == 'CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT'
    successful(engine,b,cb,'canon_decide',{'exchange_id':pending['exchange_id'],'envelope_digest':pending['envelope_digest'],
        'expected_version':1,'decision':'admit','reason':'Explicit receiver acceptance of this correction','incompatible_input_decision':'ACCEPT'})
    assert call(engine,b,cb,'canon_backfire',args).error.code == 'CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT'
    locator = {'source_project_id':b.project_id,'exchange_id':proposed['exchange_id'],'envelope_digest':proposed['envelope_digest']}
    preview = successful(engine,c,cc,'canon_packet_classify',locator)
    assert preview['classification'] == 'undefined_or_incompatible' and preview['reasons'] == ['CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT']
    assert call(engine,c,cc,'canon_receive',locator).error.code == 'CANON_BACKFIRE_REQUIRES_ADMITTED_INPUT'
    assert call(engine,b,cb,'canon_backfire',updated).error.code == 'CANON_BACKFIRE_STALE_REVISION_BLOCKED'
    assert successful(engine,b,cb,'canon_backfire',{**updated,'requested_revision':3})['state'] == 'sealed'


def test_generic_backfire_cannot_bypass_typed_conditional_guards(three):
    engine, (a,b,_), (_,cb,_), (pa,pb,_) = three
    original, _ = prepare(three)
    message = {'sender_id':pb,'receiver_id':pa,'destination_project_id':a.project_id,
        'kind':'backfire','reply_to':original['exchange_id'],'payload':{'summary':'A generic retry'}}
    assert call(engine,b,cb,'canon_send',message).error.code == 'CANON_TYPED_BACKFIRE_REQUIRED'
    preview = successful(engine,b,cb,'canon_classify',message)
    assert preview['reasons'] == ['CANON_TYPED_BACKFIRE_REQUIRED'] and not preview['automatic_admission_permitted']


def test_backfire_send_and_semantic_record_rollback_together(three, monkeypatch):
    from evidence_lane_plugin.storage import LaneStore

    engine, (_,b,_), (_,cb,_), _ = three
    _, args = prepare(three)
    original = LaneStore.append_receipt
    def fail(self, kind, *values, **options):
        if kind == 'canon_backfire_proposed':
            raise RuntimeError('Injected failure after sealing and operation insertion')
        return original(self,kind,*values,**options)
    with monkeypatch.context() as patch:
        patch.setattr(LaneStore,'append_receipt',fail)
        assert call(engine,b,cb,'canon_backfire',args).status == 'error'
    with b.lane('canon').connection(read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM canon_exchanges WHERE kind='backfire'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM canon_events WHERE kind IN ('backfire','backfire_replay')").fetchone()[0] == 0
    assert not successful(engine,b,cb,'canon_backfire',args)['duplicate']


def test_hash_consistent_dedup_record_cannot_attest_different_semantics(three):
    from evidence_lane_plugin.storage import json_text

    engine, (_,b,c), (_,cb,cc), _ = three
    _, args = prepare(three)
    raised = successful(engine,b,cb,'canon_backfire',args)
    with engine.project_work.mutation(b) as lease, lease.transaction('canon') as db:
        event = dict(db.execute("SELECT * FROM canon_events WHERE kind='backfire'").fetchone())
        record = json.loads(event['result_json'])
        record['operation']['identity_digest'] = '0'*64
        event['result_json'] = json_text(record)
        event.pop('digest')
        db.execute('UPDATE canon_events SET result_json=?,digest=? WHERE request_id=?',
            (event['result_json'],content_digest(event),event['request_id']))
    assert CanonStore(b).verify_history()['events_verified'] > 0
    assert call(engine,b,cb,'canon_backfire',args).error.code == 'CANON_OPERATION_INTEGRITY'
    assert call(engine,b,cb,'canon_inspect').error.code == 'CANON_OPERATION_INTEGRITY'
    assert call(engine,c,cc,'canon_receive',{'source_project_id':b.project_id,
        'exchange_id':raised['exchange_id'],'envelope_digest':raised['envelope_digest']}).error.code == 'CANON_OPERATION_INTEGRITY'


def test_receiver_cannot_restart_a_backfire_by_omitting_its_inherited_trace(three):
    engine, (_,b,c), (_,cb,cc), (_,pb,_) = three
    _, args = prepare(three)
    raised = successful(engine,b,cb,'canon_backfire',args)
    incoming = successful(engine,c,cc,'canon_receive',{'source_project_id':b.project_id,
        'exchange_id':raised['exchange_id'],'envelope_digest':raised['envelope_digest']})
    attempted = {**args,'request_id':str(uuid4()),'admitted_exchange_id':incoming['exchange_id'],
        'admitted_envelope_digest':incoming['envelope_digest'],'recipient':endpoint(b,pb),'trace':[]}
    assert call(engine,c,cc,'canon_backfire',attempted).error.code == 'CANON_BACKFIRE_ROUTE_CYCLE_BLOCKED'


def test_local_inbox_omits_operation_payload_and_preserves_each_packets_trace(three):
    from evidence_lane_plugin.canon_consequence_graph import canon_view
    from evidence_lane_plugin.lane_contract import ViewScope

    engine, (_,b,_), (ca,cb,_), (_,pb,_) = three
    _, args = prepare(three)
    peer = successful(engine,b,ca,'canon_join',{'label':'Local upstream recipient'})['participant_id']
    expected = successful(engine,b,ca,'canon_expect',{'receiver_id':peer,'contract_key':'local_backfire',
        'sender_ids':[pb],'kinds':['backfire'],'fields':{'content':'string'},'auto_admit':True})
    returning = successful(engine,b,cb,'canon_expect',{'receiver_id':pb,'contract_key':'local_return',
        'sender_ids':[peer],'kinds':['result'],'auto_admit':True})
    payload = {'summary':'Full conditional payload stays in Canon','fields':{'content':'RAW_CONDITIONAL_BODY_NOT_METADATA'}}
    args.update(recipient=endpoint(b,peer),requested_contract=expected['contract_digest'],payload=payload,
        return_route=endpoint(b,pb),return_contract=returning['contract_digest'])
    packets = [successful(engine,b,cb,'canon_backfire',{**args,'request_id':str(uuid4()),'requested_revision':revision})
        for revision in (2,3)]
    page = successful(engine,b,ca,'canon_inbox',{'receiver_id':peer})
    assert page['packet_count'] == 2 and not page['raw_payload_returned']
    assert payload['summary'] not in json.dumps(page) and payload['fields']['content'] not in json.dumps(page)
    for packet in page['packets']:
        event = next(item for item in packet['events'] if item['kind'] == 'backfire')
        assert event['result_projection'] == 'operation_metadata_without_request_payload'
        assert event['result']['operation']['request_digest'] == event['input_digest']
        with b.lane('canon').connection(read_only=True) as db:
            record = json.loads(db.execute('SELECT result_json FROM canon_events WHERE request_id=?',(event['request_id'],)).fetchone()[0])
        assert record['operation']['request']['payload']['fields'] == payload['fields']
        assert content_digest(record) == event['stored_result_digest']
    graph = canon_view(b,ViewScope())
    traces = [edge for edge in graph['edges'] if edge['kind'] == 'CORRECTION_TRACE']
    assert len(traces) == 4
    for packet in packets:
        assert [edge['evidence']['sequence'] for edge in traces if edge['evidence']['exchange_id'] == packet['exchange_id']] == [1,2]


def test_result_dedup_uses_edge_payload_and_conflicts_on_another_input(projects):
    engine, a,b,ca,cb,pa,pb = projects
    target = successful(engine,b,cb,'canon_expect',{'receiver_id':pb,'contract_key':'input',
        'sender_endpoints':[endpoint(a,pa)],'kinds':['requirements'],'auto_admit':True})
    return_contract = successful(engine,a,ca,'canon_expect',{'receiver_id':pa,'contract_key':'return',
        'sender_endpoints':[endpoint(b,pb)],'kinds':['result'],'auto_admit':True})
    edge = successful(engine,a,ca,'canon_task_edge_register',{'source_id':pa,'destination':endpoint(b,pb),
        'contract_digest':target['contract_digest'],'expected_return_contract':return_contract['contract_digest'],
        'schema_digest':content_digest(CanonPayload.model_json_schema())})
    successful(engine,b,cb,'canon_task_edge_bind',{'source_project_id':a.project_id,'edge_id':edge['edge_id'],'edge_digest':edge['edge_digest']})
    inputs = []
    for number in (1,2):
        sealed = successful(engine,a,ca,'canon_send',{'sender_id':pa,'receiver_id':pb,'destination_project_id':b.project_id,
            'kind':'requirements','payload':{'summary':f'Input {number}'},'expected_contract':target['contract_digest'],
            'return_contract':return_contract['contract_digest'],'edge_id':edge['edge_id']})
        inputs.append(successful(engine,b,cb,'canon_receive',{'source_project_id':a.project_id,
            'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}))
    args = {'request_id':str(uuid4()),'edge_id':edge['edge_id'],'reply_to':inputs[0]['exchange_id'],
        'payload':{'summary':'The same verified result'},'expires_at':(datetime.now(UTC)+timedelta(hours=1)).isoformat()}
    first = successful(engine,b,cb,'canon_task_result',args)
    alias = {**args,'request_id':str(uuid4()),'expires_at':(datetime.now(UTC)+timedelta(hours=2)).isoformat()}
    replay = successful(engine,b,cb,'canon_task_result',alias)
    assert replay['duplicate'] and replay['envelope_digest'] == first['envelope_digest']
    assert successful(engine,b,cb,'canon_task_result',alias)['duplicate']
    assert call(engine,b,ca,'canon_task_result',alias).error.code == 'CANON_OWNER_MISMATCH'
    assert call(engine,b,cb,'canon_task_result',{**args,'request_id':str(uuid4()),'reply_to':inputs[1]['exchange_id']}).error.code == 'CANON_TASK_RESULT_DEDUP_CONFLICT'
    assert successful(engine,b,cb,'canon_inspect')['objects_verified']['conditional_operations'] == 1
