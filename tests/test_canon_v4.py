# ruff: noqa: F811 -- imported pytest fixtures are resolved by parameter name.
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import (
    CanonDecide,
    CanonExpected,
    CanonJoin,
    CanonPayload,
    CanonRead,
    CanonSend,
    CanonStore,
)
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.project_memory import (
    MemoryIngest,
    MemoryLocator,
    MemoryRead,
    MemoryReference,
    ProjectMemory,
)
from evidence_lane_plugin.sdk import ActionRequest
from pydantic import ValidationError

from tests.test_code_profile_v4 import code_system, create_plan  # noqa: F401


@pytest.fixture
def system(code_system):
    engine,project,client=code_system
    return engine,project,None,client


def plan(system):
    engine,project,_,client=system
    create_plan((engine,project,client))
    return PlanStore(project).task('code-0',expected_revision=1)


@pytest.fixture
def pair(system):
    engine,store,_,sender=system
    _,receiver=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','write'])]))
    sdk=PublicActionSDKDispatcher(engine)
    def invoke(session,action,arguments):
        payload=arguments.model_dump(mode='json') if hasattr(arguments,'model_dump') else arguments
        return sdk.execute(ActionRequest(action=action,project_id=store.project_id,arguments=payload),session)
    first=invoke(sender,'canon_join',CanonJoin(label='Source task',reported_host_task_id=str(uuid4())))
    second=invoke(receiver,'canon_join',CanonJoin(label='Receiver task'))
    assert first.status==second.status=='ok', (first.error,second.error)
    return invoke,sender,receiver,first.result['participant_id'],second.result['participant_id'],system


def send(pair,**changes):
    invoke,sender,_receiver,source,target,_=pair
    data={'sender_id':source,'receiver_id':target,'kind':'requirements','payload':{'summary':'Bounded requirement'}}
    data.update(changes)
    request=CanonSend.model_validate(data)
    result=invoke(sender,'canon_send',request)
    assert result.status=='ok',result.error
    return result,request


def decide(pair,result,decision='admit',**changes):
    invoke,_,receiver,_,_,_=pair
    data={'exchange_id':result.result['exchange_id'],'envelope_digest':result.result['envelope_digest'],'expected_version':1,
          'decision':decision,'reason':'Receiver reviewed the scoped input.'}
    data['incompatible_input_decision']='ACCEPT' if decision=='admit' else None
    data.update(changes)
    request=CanonDecide(**data)
    response=invoke(receiver,'canon_decide',request)
    assert response.status=='ok',response.error
    return response,request


def test_sender_cannot_impersonate_receiver_and_admission_does_not_change_plan(pair):
    invoke,sender,receiver,source,target,system=pair
    plan(system)
    before=PlanStore(system[1]).snapshot().model_dump()
    sent,request=send(pair)
    assert sent.result['state']=='received'
    assert invoke(sender,'canon_send',request).result['duplicate'] is True
    forged=request.model_copy(update={'request_id':str(uuid4()),'sender_id':target,'receiver_id':source})
    assert invoke(sender,'canon_send',forged).error.code=='CANON_OWNER_MISMATCH'
    decision=CanonDecide(exchange_id=sent.result['exchange_id'],envelope_digest=sent.result['envelope_digest'],expected_version=1,decision='admit',reason='Explicit receiver acceptance of the undefined input',incompatible_input_decision='ACCEPT')
    assert invoke(sender,'canon_decide',decision).error.code=='CANON_OWNER_MISMATCH'
    admitted=invoke(receiver,'canon_decide',decision)
    assert admitted.result['state']=='admitted' and admitted.result['source_write_granted'] is False
    assert PlanStore(system[1]).snapshot().model_dump()==before
    assert invoke(receiver,'canon_decide',decision).result==admitted.result
    assert CanonStore(system[1]).verify_history()['events_verified']==4
    assert CanonStore(system[1]).read().participants[0]['native_task_attestation']=='not_provided'


def test_receiver_expected_contract_is_typed_versioned_and_exact(pair):
    invoke,sender,receiver,source,target,_system=pair
    contract=CanonExpected(receiver_id=target,contract_key='evidence',sender_ids=[source],kinds=['evidence'],fields={'count':'integer'},auto_admit=True)
    assert invoke(sender,'canon_expect',contract).error.code=='CANON_OWNER_MISMATCH'
    registered=invoke(receiver,'canon_expect',contract)
    assert registered.status=='ok',registered.error
    digest=registered.result['contract_digest']
    good=CanonSend(sender_id=source,receiver_id=target,kind='evidence',payload=CanonPayload(summary='Two observations',fields={'count':2}),expected_contract=digest)
    accepted=invoke(sender,'canon_send',good)
    assert accepted.result['state']=='admitted'
    wrong=good.model_copy(update={'request_id':str(uuid4()),'payload':CanonPayload(summary='Wrong type',fields={'count':True})})
    incompatible=invoke(sender,'canon_send',wrong)
    assert incompatible.result['state']=='received'
    assert incompatible.result['compatibility_reasons']==['CANON_PAYLOAD_TYPE_MISMATCH']
    assert incompatible.result['receiver_decision_required']
    missing_accept=CanonDecide(exchange_id=incompatible.result['exchange_id'],envelope_digest=incompatible.result['envelope_digest'],
        expected_version=1,decision='admit',reason='No explicit exception decision')
    assert invoke(receiver,'canon_decide',missing_accept).error.code=='CANON_INPUT_ACCEPT_REQUIRED'
    changed=contract.model_copy(update={'request_id':str(uuid4()),'expected_version':1,'active':False})
    assert invoke(receiver,'canon_expect',changed).result['version']==2
    stale=invoke(sender,'canon_send',good.model_copy(update={'request_id':str(uuid4())}))
    assert stale.result['state']=='received' and stale.result['compatibility_reasons']==['CANON_CONTRACT_MISMATCH']
    assert invoke(receiver,'canon_expect',contract.model_copy(update={'request_id':str(uuid4())})).error.code=='CANON_CONTRACT_VERSION_CONFLICT'


def test_pending_correction_preserves_original_until_receiver_admission(pair):
    invoke,sender,_receiver,source,target,system=pair
    original,_=send(pair)
    decide(pair,original)
    correction,_=send(pair,kind='correction',supersedes=original.result['exchange_id'],payload={'summary':'Corrected requirement'})
    states={item['exchange_id']:item['state'] for item in CanonStore(system[1]).read().exchanges}
    assert states[original.result['exchange_id']]=='admitted' and states[correction.result['exchange_id']]=='received'
    decide(pair,correction)
    states={item['exchange_id']:item['state'] for item in CanonStore(system[1]).read().exchanges}
    assert states[original.result['exchange_id']]=='superseded' and states[correction.result['exchange_id']]=='admitted'
    bad=CanonSend(sender_id=source,receiver_id=target,kind='correction',supersedes=original.result['exchange_id'],payload=CanonPayload(summary='Duplicate correction'))
    assert invoke(sender,'canon_send',bad).error.code=='CANON_CORRECTION_MISMATCH'


def test_result_backfire_return_route_and_clarification_keep_ownership(pair):
    invoke,sender,receiver,source,target,_system=pair
    original,_=send(pair)
    response=CanonSend(sender_id=target,receiver_id=source,kind='backfire',reply_to=original.result['exchange_id'],payload=CanonPayload(summary='Missing required input',fields={'failure_class':'missing_information'}))
    assert invoke(receiver,'canon_send',response).error.code=='CANON_TYPED_BACKFIRE_REQUIRED'
    response=response.model_copy(update={'request_id':str(uuid4()),'kind':'clarification'})
    assert invoke(receiver,'canon_send',response).error.code=='CANON_RETURN_REQUIRES_INPUT'
    decide(pair,original,decision='clarify')
    returned=invoke(receiver,'canon_send',response)
    assert returned.status=='ok',returned.error
    wrong=response.model_copy(update={'request_id':str(uuid4()),'sender_id':source,'receiver_id':target})
    assert invoke(sender,'canon_send',wrong).error.code=='CANON_RETURN_ROUTE_MISMATCH'
    expired=CanonSend(sender_id=source,receiver_id=target,kind='evidence',payload=CanonPayload(summary='Expired'),expires_at=(datetime.now(UTC)-timedelta(seconds=5)).isoformat())
    assert invoke(sender,'canon_send',expired).error.code=='CANON_EXCHANGE_EXPIRED'


def test_evidence_checks_and_failed_write_rollback_are_atomic(pair,monkeypatch):
    invoke,sender,_receiver,source,target,system=pair
    absent=MemoryReference(kind='source_object',key='0'*64,digest='0'*64,profile='code',lane_id='local_code')
    bad=CanonSend(sender_id=source,receiver_id=target,kind='evidence',payload=CanonPayload(summary='Missing source',references=[absent]))
    assert invoke(sender,'canon_send',bad).error.code=='MEMORY_SOURCE_MISMATCH'
    store=system[1]
    before=CanonStore(store).verify_history()
    before_root=store.pv_head()
    before_lanes={item['lane_id']:item for item in store.lane_catalog() if item['lane_id']!='receipts'}
    with store.lane('receipts').connection(read_only=True) as connection:
        before_receipts={row[0] for row in connection.execute('SELECT receipt_id FROM receipts')}
    receipt_owner=store.lane('canon').__class__
    original=receipt_owner.append_receipt
    def fail(self,kind,*args,**kwargs):
        result=original(self,kind,*args,**kwargs)
        if kind=='canon_exchange_received':raise RuntimeError('Injected receipt failure')
        return result
    monkeypatch.setattr(receipt_owner,'append_receipt',fail)
    request=CanonSend(sender_id=source,receiver_id=target,kind='evidence',payload=CanonPayload(summary='Will roll back'))
    response=invoke(sender,'canon_send',request)
    assert response.status=='error' and response.error.code=='TOOL_ADAPTER_FAILED'
    assert 'Injected receipt failure' not in response.model_dump_json()
    assert CanonStore(store).read().exchanges==[]
    assert CanonStore(store).verify_history()==before
    assert store.pv_head()['revision']==before_root['revision']+1
    assert {item['lane_id']:item for item in store.lane_catalog() if item['lane_id']!='receipts'}==before_lanes
    with store.lane('receipts').connection(read_only=True) as connection:
        added=[row['kind'] for row in connection.execute('SELECT receipt_id,kind FROM receipts') if row['receipt_id'] not in before_receipts]
    assert added==['writer_acquired']


def test_memory_references_keep_canon_state_and_queries_are_read_only(pair):
    invoke,sender,receiver,_source,_target,system=pair
    original,_=send(pair)
    ref=MemoryReference(kind='canon_exchange',key=original.result['exchange_id'],digest=original.result['envelope_digest'])
    memory=MemoryIngest(locators=[MemoryLocator(reference=ref,label='Canon requirement')])
    assert invoke(sender,'memory_ingest',memory).status=='ok'
    before=system[1].database.read_bytes()
    page=invoke(receiver,'canon_read',{'include_payload':True})
    assert page.result['exchanges'][0]['envelope']['payload_provenance']=='agent_report'
    assert ProjectMemory(system[1]).read().locators[0]['source_state']=='received'
    assert system[1].database.read_bytes()==before
    decide(pair,original,decision='reject')
    assert ProjectMemory(system[1]).read().locators==[]
    assert ProjectMemory(system[1]).read(MemoryRead(include_history=True)).locators[0]['source_state']=='rejected'


def test_cross_project_claims_and_invalid_private_payload_do_not_admit(pair,tmp_path):
    _invoke,sender,_receiver,source,target,system=pair
    with pytest.raises(ValidationError):CanonPayload(summary='password=not-admissible')
    with pytest.raises(ValidationError):CanonPayload(summary='Text',fields={'private_reasoning':'not visible'})
    with pytest.raises(ValidationError):CanonPayload(summary='Text',fields={'nested':{'value':True}})
    other=system[0].directory.register(tmp_path/'other-state',source_root=system[1].source_root,create=True,read_only=False)
    store=system[0].directory.open(other['project_id'],write=True)
    before=store.database.read_bytes()
    assert CanonStore(store).read().participants==[]
    assert store.database.read_bytes()==before
    with system[0].project_work.mutation(store) as lease, pytest.raises(Exception) as error:
        CanonStore(store).send(CanonSend(sender_id=source,receiver_id=target,kind='requirements',payload=CanonPayload(summary='Cross project')),lease,actor_id=sender.client_id)
    assert error.value.code=='CANON_PARTICIPANT_NOT_FOUND'


def test_envelope_integrity_and_byte_budget(pair):
    invoke,sender,_receiver,_source,_target,system=pair
    for index in range(6):
        send(pair,payload={'summary':'Large '+str(index),'fields':{'content':'x'*3900,'second':'y'*3900}})
    page=CanonStore(system[1]).read(CanonRead(include_payload=True,limit=2))
    assert len(page.exchanges)==2 and page.truncated
    next_page=CanonStore(system[1]).read(CanonRead(after_sequence=page.last_sequence,limit=2))
    assert len(next_page.exchanges)==2 and next_page.exchanges[0]['sequence']>page.last_sequence
    assert len(page.model_dump_json().encode())<=131072
    with system[0].project_work.mutation(system[1]) as lease,lease.transaction('canon') as connection:
        connection.execute("UPDATE canon_exchanges SET kind='result' WHERE sequence=1")
    assert invoke(sender,'canon_read',{}).error.code=='CANON_ENVELOPE_INTEGRITY'


def test_return_contract_is_pinned_to_source_expectation_and_chain_is_bounded(pair):
    invoke,sender,receiver,source,target,_system=pair
    registered=invoke(sender,'canon_expect',CanonExpected(receiver_id=source,contract_key='return_value',sender_ids=[target],kinds=['result'],fields={'ok':'boolean'},auto_admit=True))
    assert registered.status=='ok'
    digest=registered.result['contract_digest']
    original,_=send(pair,return_contract=digest)
    decide(pair,original)
    result=CanonSend(sender_id=target,receiver_id=source,kind='result',reply_to=original.result['exchange_id'],payload=CanonPayload(summary='Done',fields={'ok':True}))
    assert invoke(receiver,'canon_send',result).error.code=='CANON_RETURN_CONTRACT_MISMATCH'
    response=invoke(receiver,'canon_send',result.model_copy(update={'expected_contract':digest}))
    assert response.result['state']=='admitted'
    last=response
    from_id,to_id,actor,recipient=source,target,sender,receiver
    for depth in range(2,9):
        req=CanonSend(sender_id=from_id,receiver_id=to_id,kind='clarification',reply_to=last.result['exchange_id'],payload=CanonPayload(summary='Bounded clarification'))
        last=invoke(actor,'canon_send',req)
        assert last.status=='ok',last.error
        accepted=invoke(recipient,'canon_decide',CanonDecide(exchange_id=last.result['exchange_id'],envelope_digest=last.result['envelope_digest'],expected_version=1,decision='admit',reason='Continue bounded clarification',incompatible_input_decision='ACCEPT'))
        assert accepted.status=='ok'
        from_id,to_id,actor,recipient=to_id,from_id,recipient,actor
    over=invoke(actor,'canon_send',CanonSend(sender_id=from_id,receiver_id=to_id,kind='clarification',reply_to=last.result['exchange_id'],payload=CanonPayload(summary='Ninth return')))
    assert over.error.code=='CANON_RETURN_DEPTH'


def test_state_version_corruption_is_rejected_before_admission(pair):
    invoke,sender,_receiver,_source,_target,system=pair
    result,_=send(pair)
    with system[0].project_work.mutation(system[1]) as lease,lease.transaction('canon') as connection:
        connection.execute("UPDATE canon_exchanges SET state='admitted' WHERE exchange_id=?",(result.result['exchange_id'],))
    assert invoke(sender,'canon_read',{}).error.code=='CANON_STATE_INTEGRITY'


def test_canon_input_arrives_during_owned_delta_without_plan_steer(pair,monkeypatch):
    from threading import Event

    from tests.test_task_mode_binding_v4 import finish

    _,_,_,_,_,system=pair
    engine,project,_,client=system
    (project.source_root/'app.py').write_text('def value():\n    return 42\n')
    view=plan(system)
    waiting,released=Event(),Event()
    finish_delta=engine.delta_exit.finish
    def bounded_pause(*arguments):
        waiting.set()
        assert released.wait(20),'Canon capture did not finish within its test budget.'
        return finish_delta(*arguments)
    monkeypatch.setattr(engine.delta_exit,'finish',bounded_pause)
    entered=PublicActionSDKDispatcher(engine).execute(ActionRequest(action='delta_enter',
        project_id=project.project_id,expected_revision=1,arguments={'task_id':'code-0','plan_revision':1,
        'contract_digest':view.contract_digest,'action':'code_index','arguments':{'paths':['.']}}),client)
    assert entered.status=='queued',entered.error
    try:
        assert waiting.wait(20),'The registered Code Delta did not reach verification after its worker operation.'
        before=PlanStore(system[1]).snapshot().model_dump()
        message,_=send(pair,kind='plan_proposal',payload={'summary':'Proposal is recorded while current work continues'})
        decide(pair,message)
        assert PlanStore(system[1]).snapshot().model_dump()==before
        assert CanonStore(system[1]).read().exchanges[0]['state']=='admitted'
    finally:
        released.set()
    assert finish((engine,project,client),entered)['state']=='verified'
    assert engine.workers.status()['succeeded_operations']>=1
