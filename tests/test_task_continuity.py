import json
from datetime import UTC, datetime, timedelta

import pytest
from evidence_lane_plugin.canon_task_graph import CanonJoin, CanonPayload, CanonSend, CanonStore
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.project_memory import MemoryCheckpoint
from evidence_lane_plugin.storage import LaneStore
from evidence_lane_plugin.task_binding_registry import (
    ContinuationAccept,
    ContinuationCancel,
    ContinuationOffer,
    TaskContinuity,
)

from tests import test_canon_v4, test_delta_entry
from tests.test_delta_entry import plan

pair = test_canon_v4.pair
system = test_delta_entry.system


def offer(pair,**changes):
    invoke,sender,_receiver,source,target,system=pair
    view=PlanStore(system[1]).task('first',expected_revision=1)
    data={'participant_id':source,'destination_participant_id':target,'task_id':'first','plan_revision':1,'contract_digest':view.contract_digest}
    data.update(changes)
    request=ContinuationOffer(**data)
    result=invoke(sender,'continuation_offer',request)
    assert result.status=='ok',result.error
    return result,request


def accept(pair,result,**changes):
    invoke,_sender,receiver,_source,_target,_system=pair
    request=ContinuationAccept(continuation_id=result.result['continuation_id'],continuation_digest=result.result['continuation_digest'],**changes)
    return invoke(receiver,'continuation_accept',request),request


def test_exact_transfer_preserves_plan_and_closes_source_write_access(pair):
    invoke,sender,receiver,source,target,system=pair
    plan(system)
    extra=invoke(sender,'canon_join',CanonJoin(label='Second source participant'))
    assert extra.status=='ok'
    before=PlanStore(system[1]).snapshot().model_dump()
    offered,_=offer(pair)
    result,request=accept(pair,offered)
    assert result.status=='ok',result.error
    assert set(result.result['transferred_participant_ids'])=={source,extra.result['participant_id']}
    assert result.result['owner_generation']==2
    assert result.result['native_task_attestation']=='not_provided' and result.result['host_session_attached'] is False
    assert invoke(receiver,'continuation_accept',request).result==result.result
    assert PlanStore(system[1]).snapshot().model_dump()==before
    assert invoke(sender,'continuation_read',{}).status=='ok'
    denied=invoke(sender,'canon_join',CanonJoin(label='Must not reopen source work'))
    assert denied.error.code=='SOURCE_CLIENT_CLOSEOUT_ONLY'
    sent=invoke(receiver,'canon_send',CanonSend(sender_id=source,receiver_id=target,kind='evidence',payload=CanonPayload(summary='Continued participant records new evidence')))
    assert sent.status=='ok',sent.error
    with system[1].lane('chat_lineage').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM continuation_bindings').fetchone()[0]==2
        assert connection.execute('SELECT count(*) FROM continuation_retired_clients').fetchone()[0]==1


def test_wrong_receiver_stale_digest_and_expiry_do_not_transfer(pair):
    invoke,sender,receiver,source,_target,system=pair
    plan(system)
    offered,_=offer(pair)
    req=ContinuationAccept(continuation_id=offered.result['continuation_id'],continuation_digest=offered.result['continuation_digest'])
    assert invoke(sender,'continuation_accept',req).error.code=='CONTINUATION_RECEIVER_MISMATCH'
    assert invoke(receiver,'continuation_accept',req.model_copy(update={'continuation_digest':'0'*64})).error.code=='CONTINUATION_IDENTITY_MISMATCH'
    future=datetime.now(UTC)+timedelta(days=2)
    with system[0].project_work.mutation(system[1]) as lease,pytest.raises(LaneError) as error:
        TaskContinuity(system[1],clock=lambda:future).accept(req,lease,actor_id=receiver.client_id)
    assert error.value.code=='CONTINUATION_EXPIRED'
    assert next(item for item in CanonStore(system[1]).read().participants if item['participant_id']==source)['owner_client_id']==sender.client_id


def test_visible_input_change_requires_fresh_offer_and_source_can_cancel(pair):
    invoke,sender,receiver,_source,_target,system=pair
    plan(system)
    offered,_=offer(pair)
    with system[0].project_work.mutation(system[1],kind='capture') as lease:
        ChatLineage(system[1]).append(LineageRecord(kind='prompt',payload={'text':'New visible direction arrived'}),lease,client_id=sender.client_id)
    result,_=accept(pair,offered)
    assert result.error.code=='CONTINUATION_LINEAGE_CHANGED'
    cancellation=ContinuationCancel(continuation_id=offered.result['continuation_id'],continuation_digest=offered.result['continuation_digest'],reason='Review the new input')
    assert invoke(receiver,'continuation_cancel',cancellation).error.code=='CANON_OWNER_MISMATCH'
    assert invoke(sender,'continuation_cancel',cancellation).result['state']=='cancelled'
    fresh,_=offer(pair)
    assert accept(pair,fresh)[0].status=='ok'


def test_native_attestation_requirement_fails_before_any_continuation_write(pair):
    invoke,sender,_receiver,source,target,system=pair
    view=plan(system)
    before=system[1].database.read_bytes()
    req=ContinuationOffer(participant_id=source,destination_participant_id=target,task_id='first',plan_revision=1,contract_digest=view.contract_digest,require_native_attestation=True)
    result=invoke(sender,'continuation_offer',req)
    assert result.error.code=='NATIVE_TASK_ATTESTATION_UNAVAILABLE'
    assert system[1].database.read_bytes()==before


def test_failed_receipt_rolls_back_all_owners_and_source_closeout(pair,monkeypatch):
    invoke,sender,_receiver,_source,_target,system=pair
    plan(system)
    offered,_=offer(pair)
    original=LaneStore.append_receipt
    before_owners=CanonStore(system[1]).read().participants
    injected=[]
    def fail(self,kind,*args,**kwargs):
        value=original(self,kind,*args,**kwargs)
        if kind=='project_participant_continued':
            injected.append(kind)
            raise RuntimeError('Injected continuation failure')
        return value
    monkeypatch.setattr(LaneStore,'append_receipt',fail)
    failed,_=accept(pair,offered)
    assert failed.error and failed.error.code=='TOOL_ADAPTER_FAILED', failed
    assert injected==['project_participant_continued']
    assert CanonStore(system[1]).read().participants==before_owners
    assert invoke(sender,'canon_join',CanonJoin(label='Source remains authorized')).status=='ok'
    with system[1].lane('chat_lineage').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM continuation_bindings').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM continuation_retired_clients').fetchone()[0]==0
        assert connection.execute('SELECT state FROM continuation_offers').fetchone()[0]=='offered'
        assert json.loads(connection.execute('SELECT body_json FROM continuation_offers').fetchone()[0])['canon_checkpoint_digest']==offered.result['canon_checkpoint_digest']
    with system[1].lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='project_participant_continued'").fetchone()[0]==0


def test_owner_set_change_and_uncertain_jobs_block_acceptance(pair):
    from evidence_lane_plugin.jobs import JobQueue
    from evidence_lane_plugin.sdk import ActionRequest
    invoke,sender,_receiver,_source,_target,system=pair
    plan(system)
    offered,_=offer(pair)
    invoke(sender,'canon_join',CanonJoin(label='New source role'))
    assert accept(pair,offered)[0].error.code=='CONTINUATION_OWNERSHIP_CHANGED'
    with system[0].project_work.mutation(system[1]) as lease:
        queue=JobQueue(system[1]);queue.initialize(lease)
        queue.enqueue(ActionRequest(action='fixture_hash',project_id=system[1].project_id,expected_revision=1),sender.client_id,lease)
    assert accept(pair,offered)[0].error.code=='CONTINUATION_WORK_NOT_QUIESCENT'


def test_memory_checkpoint_is_exact_and_reads_do_not_mutate(pair):
    invoke,sender,receiver,_source,_target,system=pair
    view=plan(system)
    checkpoint=invoke(sender,'memory_checkpoint',MemoryCheckpoint(task_id='first',plan_revision=1,contract_digest=view.contract_digest,locator_ids=[]))
    assert checkpoint.status=='ok'
    offered,_=offer(pair,memory_checkpoint=checkpoint.result['checkpoint_digest'])
    before=system[1].database.read_bytes()
    read=invoke(receiver,'continuation_read',{})
    assert read.result['offers'][0]['binding']['memory_checkpoint']==checkpoint.result['checkpoint_digest']
    assert system[1].database.read_bytes()==before
    assert accept(pair,offered)[0].status=='ok'


def test_continuation_context_links_owners_and_rejects_unaccepted_offers(pair):
    invoke,sender,receiver,_source,_target,system=pair
    view=plan(system)
    with system[0].project_work.mutation(system[1]) as lease:
        ChatLineage(system[1]).append(LineageRecord(kind='prompt',payload={'text':'Visible original request'}),lease,client_id=sender.client_id)
    checkpoint=invoke(sender,'memory_checkpoint',MemoryCheckpoint(task_id='first',plan_revision=1,contract_digest=view.contract_digest,locator_ids=[]))
    offered,_=offer(pair,memory_checkpoint=checkpoint.result['checkpoint_digest'])
    args={'continuation_id':offered.result['continuation_id'],'continuation_digest':offered.result['continuation_digest']}
    assert invoke(receiver,'continuation_context',args).error.code=='CONTINUATION_NOT_ACCEPTED'
    assert accept(pair,offered)[0].status=='ok'
    before=system[1].database.read_bytes()
    context=invoke(receiver,'continuation_context',args)
    assert context.status=='ok',context.error
    assert context.result['source_client_id']==sender.client_id and context.result['current_owner']['owner_client_id']==receiver.client_id
    assert context.result['plan_task']['contract_compatible'] is True
    assert context.result['lineage_locators'][0]['provenance']=='agent_report'
    assert 'payload' not in context.result['lineage_locators'][0]
    assert context.result['memory']['checkpoint']['source_client_id']==sender.client_id
    assert system[1].database.read_bytes()==before
    assert TaskContinuity(system[1]).verify_history()['events_verified']==2


def test_previously_authenticated_waiting_source_write_is_rechecked_after_cutover(pair,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    invoke,sender,_receiver,_source,_target,system=pair
    plan(system)
    offered,_=offer(pair)
    entered=threading.Event();release=threading.Event()
    original=system[0].project_work.mutation
    @contextmanager
    def delayed(*args,**kwargs):
        if threading.current_thread().name.startswith('stale-source'):
            entered.set()
            assert release.wait(5)
        with original(*args,**kwargs) as lease:yield lease
    monkeypatch.setattr(system[0].project_work,'mutation',delayed)
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='stale-source') as pool:
        pending=pool.submit(invoke,sender,'canon_join',CanonJoin(label='Late write must not succeed'))
        assert entered.wait(5)
        try:
            assert accept(pair,offered)[0].status=='ok'
        finally:
            release.set()
        assert pending.result(5).error.code=='SOURCE_CLIENT_CLOSEOUT_ONLY'
    assert all(item['label']!='Late write must not succeed' for item in CanonStore(system[1]).read().participants)


def test_successive_continuations_preserve_each_participant_history(pair):
    from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
    invoke,_sender,receiver,source,target,system=pair
    plan(system)
    first,_=offer(pair)
    accepted,_=accept(pair,first)
    assert accepted.status=='ok'
    _,third=system[0].clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=system[1].project_id,permissions=['read','write'])]))
    endpoint=invoke(third,'canon_join',CanonJoin(label='Third client'))
    assert endpoint.status=='ok'
    view=PlanStore(system[1]).task('first',expected_revision=1)
    second=invoke(receiver,'continuation_offer',ContinuationOffer(participant_id=source,destination_participant_id=endpoint.result['participant_id'],task_id='first',plan_revision=1,contract_digest=view.contract_digest))
    assert second.status=='ok',second.error
    moved=invoke(third,'continuation_accept',ContinuationAccept(continuation_id=second.result['continuation_id'],continuation_digest=second.result['continuation_digest']))
    assert moved.status=='ok',moved.error
    assert moved.result['owner_generation']==3
    assert set(moved.result['transferred_participant_ids'])=={source,target}
    assert invoke(receiver,'canon_join',CanonJoin(label='Second client cannot reopen')).error.code=='SOURCE_CLIENT_CLOSEOUT_ONLY'
    history=invoke(third,'continuation_read',{}).result['offers']
    assert [item['result']['owner_generation'] for item in history]==[2,3]
    assert TaskContinuity(system[1]).verify_history()['events_verified']==4
