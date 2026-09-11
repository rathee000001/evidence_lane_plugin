
from evidence_lane_plugin.canon_runtime_continuity import (
    ContinuationContextRequest,
    read_continuation_context,
)
from evidence_lane_plugin.canon_task_graph import CanonJoin, CanonStore
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.database_recovery import (
    BackupRequest,
    DatabaseRecovery,
    restore_backup_offline,
)
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition, content_digest
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.task_binding_registry import (
    ContinuationAccept,
    ContinuationCancel,
    ContinuationOffer,
    ContinuationRecoveryOffer,
    TaskContinuity,
)

from tests import test_canon_v4, test_session_v4
from tests.test_task_continuity import accept, offer

pair = test_canon_v4.pair
system = test_session_v4.selected


def plan(system):
    engine, store, _, client = system
    spec = engine.registry.get('code_index')
    task = TaskDefinition(task_id='first',title='Inspect selected Code',requested_outcome='Verify the selected source',
        profile='code',allowed_actions=['code_index'],permitted_paths=['.'],permitted_tools=list(spec.required_tools),
        acceptance_checks=list(spec.verification_checks))
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Continuation fixture',tasks=[task]),lease,actor_id=client.client_id)
    return PlanStore(store).task('first',expected_revision=1)


def recovery_request(store,source,target):
    participants={item['participant_id']:item for item in CanonStore(store).read().participants}
    task=PlanStore(store).task('first',expected_revision=1)
    return ContinuationRecoveryOffer(offer=ContinuationOffer(participant_id=source,destination_participant_id=target,
        task_id='first',plan_revision=1,contract_digest=task.contract_digest),
        expected_source_participant_digest=content_digest(participants[source]),
        expected_destination_participant_digest=content_digest(participants[target]),
        reason='Recover explicitly selected participant ownership after local session loss.')


def test_admin_reassignment_requires_exact_participants_and_destination_acceptance(pair):
    invoke,sender,receiver,source,target,system=pair
    engine,store,token,_=system
    plan(system)
    _,admin=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','write','admin'])]))
    req=recovery_request(store,source,target)
    assert invoke(receiver,'continuation_recovery_offer',req).error.code=='PROJECT_NOT_SELECTED'
    engine.clients.disconnect(token)
    before=PlanStore(store).snapshot().model_dump()
    wrong=req.model_copy(update={'expected_source_participant_digest':'0'*64})
    assert invoke(admin,'continuation_recovery_offer',wrong).error.code=='RECOVERY_PARTICIPANT_CHANGED'
    offered=invoke(admin,'continuation_recovery_offer',req)
    assert offered.status=='ok',offered.error
    assert offered.result['source_client_id']==sender.client_id
    assert invoke(admin,'continuation_recovery_offer',req).result==offered.result
    assert next(item for item in CanonStore(store).read().participants if item['participant_id']==source)['owner_client_id']==sender.client_id
    acceptance=ContinuationAccept(continuation_id=offered.result['continuation_id'],continuation_digest=offered.result['continuation_digest'])
    assert invoke(admin,'continuation_accept',acceptance).error.code=='CONTINUATION_RECEIVER_MISMATCH'
    accepted=invoke(receiver,'continuation_accept',acceptance)
    assert accepted.status=='ok',accepted.error
    assert accepted.result['owner_client_id']==receiver.client_id and not accepted.result['host_session_attached']
    assert PlanStore(store).snapshot().model_dump()==before
    record=TaskContinuity(store).read().offers[0]
    assert record['binding']['administrative_recovery']['authorized_by_client_id']==admin.client_id


def test_admin_can_cancel_exact_unconsumed_lost_source_offer(pair):
    invoke,_sender,_receiver,_source,_target,system=pair
    engine,store,token,_=system
    plan(system)
    offered,_=offer(pair)
    engine.clients.disconnect(token)
    _,admin=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','admin'])]))
    req=ContinuationCancel(continuation_id=offered.result['continuation_id'],continuation_digest=offered.result['continuation_digest'],reason='Source session was lost; cancel this exact outstanding offer.')
    assert invoke(admin,'continuation_recovery_cancel',req.model_copy(update={'continuation_digest':'0'*64})).error.code=='CONTINUATION_IDENTITY_MISMATCH'
    assert invoke(admin,'continuation_recovery_cancel',req).result['state']=='cancelled'
    assert invoke(admin,'continuation_recovery_cancel',req).result['state']=='cancelled'
    assert TaskContinuity(store).verify_history()['events_verified']>=2


def test_recovered_state_keeps_historical_continuations_readable_without_reattaching_clients(pair,tmp_path):
    _invoke,_sender,_receiver,source,target,system=pair
    engine,store,_,_=system
    plan(system)
    offered,_=offer(pair)
    accepted,_=accept(pair,offered)
    assert accepted.status=='ok'
    with engine.project_work.mutation(store) as lease:
        saved=DatabaseRecovery(engine,store).backup(BackupRequest(destination_root=str(tmp_path/'backup')),lease,actor_id='owner')
    assert engine.stop()
    restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,tmp_path/'recovered')
    with Engine(engine.root) as restarted:
        current=restarted.directory.open(store.project_id,write=True)
        rows=TaskContinuity(current).read().offers
        assert rows[0]['state']=='accepted' and rows[0]['current_roots_match'] is False
        context=read_continuation_context(current,ContinuationContextRequest(continuation_id=offered.result['continuation_id'],
            continuation_digest=offered.result['continuation_digest']))
        assert context.state_root==str(current.root) and not context.plan_task['execution_authorized']
        _,new=restarted.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','write','admin'])]))
        sdk=PublicActionSDKDispatcher(restarted)
        def call(action,request):
            return sdk.execute(ActionRequest(action=action,project_id=store.project_id,
                arguments=request.model_dump(mode='json')),new)
        destination=call('task_evidence_participant_register',CanonJoin(label='Authenticated recovery destination'))
        assert destination.status=='ok'
        proposal=call('continuation_recovery_offer',recovery_request(current,source,destination.result['participant_id']))
        assert proposal.status=='ok',proposal.error
        result=call('continuation_accept',ContinuationAccept(continuation_id=proposal.result['continuation_id'],
            continuation_digest=proposal.result['continuation_digest']))
        assert result.status=='ok',result.error
        assert set(result.result['transferred_participant_ids'])=={source,target}
        assert result.result['owner_client_id']==new.client_id
