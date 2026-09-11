"""Source-selected modes constrain real Plan/Delta work and preserve history."""
# ruff: noqa: F811 -- pytest resolves the explicitly imported fixture by name.
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore, TaskDefinition
from evidence_lane_plugin.prompt_index import PromptIndex

from tests.test_code_profile_v4 import call, code_system  # noqa: F401


def selected_mode(system, modes=('CD',), intent='work'):
    source = call(system, 'lineage_record', {'kind':'prompt','payload':{'text':'Index the selected code source.'}})
    assert source.status == 'ok', source.error
    result = call(system, 'task_classify', {'classification_id':str(uuid4()),
        'source_event_id':source.result['event_id'], 'source_cursor':source.result['cursor'],
        'intent':intent,'focus':'Index the selected source.','workflow':'execute-project-plan' if intent=='work' else 'manage-project-plan',
        'next_action':'delta_enter' if intent=='work' else 'plan_read',
        'lanes':['local_code'], 'explicit_modes':list(modes)})
    assert result.status == 'ok', result.error
    return result.result


def task(system, binding=None, task_id='code-0'):
    spec = system[0].registry.get('code_index')
    return TaskDefinition(task_id=task_id,title='Index',requested_outcome='Verify the selected source index.',
        profile='code',allowed_actions=['code_index'],permitted_paths=['.'],
        permitted_tools=['Python','SQLite_FTS5_BM25','Python_structural_parser'],
        acceptance_checks=list(spec.verification_checks),mode_binding=binding)


def create(system, binding):
    return call(system,'plan_create',{'title':'Bound Code work','tasks':[task(system,binding).model_dump(mode='json')]})


def enter(system):
    view = PlanStore(system[1]).task('code-0',expected_revision=1)
    return call(system,'delta_enter',{'task_id':'code-0','plan_revision':1,'contract_digest':view.contract_digest,
        'action':'code_index','arguments':{'paths':['.']}},expected_revision=1)


def finish(system, admitted):
    assert admitted.status == 'queued', admitted.error
    # This fixture owns one driver. Join it before reading coordinated lane
    # publication, so the observer does not compete with its active commit.
    engine = system[0]
    with engine._admission:
        assert engine._admission.wait_for(lambda: engine._background_jobs == 0, timeout=40), 'Bound Delta driver did not finish.'
    with system[1].lane('plan').connection(read_only=True) as db:
        return dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?',(admitted.job_id,)).fetchone())


def test_exact_mode_reaches_entry_tool_result_verifier_and_exit_receipts(code_system):
    binding=selected_mode(code_system)['task_mode_binding']
    assert create(code_system,binding).status=='ok'
    row=finish(code_system,enter(code_system))
    assert row['state']=='verified',row
    project=code_system[1];store=project.lane('plan')
    entry=json.loads(store.read_object(row['entry_object']))
    result=json.loads(store.read_object(row['result_object']))
    with store.connection(read_only=True) as db,project.lane('receipts').connection(read_only=True) as receipts:
        exit_row=db.execute('SELECT * FROM delta_exits WHERE job_id=?',(row['job_id'],)).fetchone()
        receipt=json.loads(receipts.execute('SELECT body_json FROM receipts WHERE receipt_id=?',(exit_row['receipt_id'],)).fetchone()[0])
    verification=json.loads(store.read_object(exit_row['verification_object']))
    exact=entry['task_mode']
    assert exact['classification_digest']==binding['classification_digest']
    assert exact['selected_mode_ids']==['CD'] and not exact['action_effect_executed']
    assert exact==result['task_mode']==verification['task_mode']==receipt['task_mode']
    assert exact==result['tool_execution']['env_uop']['task_mode']
    selection = result['tool_execution']['env_uop']['selection_context']
    assert selection['task_id'] == 'code-0' and selection['plan_revision'] == 1
    assert selection['workflow_classes'] == ['CODE']
    assert selection['intent_basis'] == 'current_plan_task'
    assert selection['host_profile_basis'] == 'authenticated_client_report'
    assert not selection['project_reclassification_inferred']
    assert PlanStore(project).task('code-0',expected_revision=1).state=='completed'


@pytest.mark.parametrize('field',['classification_digest','source_cursor','selection_sha256','manifest_digest'])
def test_changed_reference_rejected_before_plan_creation(code_system,field):
    binding=selected_mode(code_system)['task_mode_binding'];binding[field]='0'*64
    response=create(code_system,binding)
    assert response.status=='error'
    assert response.error.code in {'TASK_MODE_SOURCE_MISMATCH','PROMPT_SOURCE_MISMATCH','TASK_MODE_SELECTION_MISMATCH'}
    assert PlanStore(code_system[1]).snapshot().state=='no_plan'


def test_mode_cannot_grant_an_action_outside_its_actual_lane(code_system):
    binding=selected_mode(code_system,modes=('PL',))['task_mode_binding']
    assert create(code_system,binding).error.code=='TASK_MODE_ACTION_SCOPE'
    assert code_system[0].workers.status()['submitted']==0


def test_informational_classification_does_not_offer_a_work_binding(code_system):
    result=selected_mode(code_system,modes=('CD',),intent='informational')
    assert result['task_mode_binding'] is None
    entry=result['entry'];mode=entry['mode_selection']['mode_governance']
    forged={k:entry[k] for k in ('classification_id','classification_digest','source_event_id','source_cursor')}
    forged.update(selection_sha256=mode['selection_sha256'],manifest_digest=mode['manifest_digest'])
    assert create(code_system,forged).error.code=='TASK_MODE_WORK_CLASSIFICATION_REQUIRED'


def test_new_unrelated_mode_classification_does_not_change_pinned_work(code_system):
    binding=selected_mode(code_system)['task_mode_binding']
    assert create(code_system,binding).status=='ok'
    selected_mode(code_system,modes=('PL',))
    row=finish(code_system,enter(code_system))
    assert row['state']=='verified',row
    assert PlanStore(code_system[1]).task('code-0',expected_revision=1).definition.mode_binding.classification_digest==binding['classification_digest']


@pytest.mark.parametrize('boundary',['admission','execution','verification'])
def test_missing_bound_source_blocks_each_execution_boundary(code_system,monkeypatch,boundary):
    binding=selected_mode(code_system)['task_mode_binding']
    assert create(code_system,binding).status=='ok'
    def unavailable(*args,**kwargs):
        raise LaneError('TASK_MODE_SOURCE_MISSING','Injected missing immutable classification.')
    if boundary=='admission':
        monkeypatch.setattr(PromptIndex,'classification',unavailable)
        assert enter(code_system).error.code=='TASK_MODE_SOURCE_MISSING'
        assert code_system[0].workers.status()['submitted']==0
        return
    engine=code_system[0]
    if boundary=='execution':
        original=engine.delta._run
        def start(*args,**kwargs):
            monkeypatch.setattr(PromptIndex,'classification',unavailable)
            return original(*args,**kwargs)
        monkeypatch.setattr(engine.delta,'_run',start)
    else:
        original=engine.delta_exit.finish
        def verify(*args,**kwargs):
            monkeypatch.setattr(PromptIndex,'classification',unavailable)
            return original(*args,**kwargs)
        monkeypatch.setattr(engine.delta_exit,'finish',verify)
    row=finish(code_system,enter(code_system))
    assert row['state']=='blocked' and row['error_code']=='TASK_MODE_SOURCE_MISSING',row
    assert PlanStore(code_system[1]).task('code-0',expected_revision=1).state=='blocked'
    with code_system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM delta_exits').fetchone()[0]==0


def test_unbound_historical_task_serialization_keeps_its_prior_digest():
    value=TaskDefinition(task_id='old',title='Existing',requested_outcome='Preserve prior Plan bytes.').model_dump(mode='json')
    assert 'mode_binding' not in value
    assert TaskDefinition.model_validate(value).model_dump(mode='json')==value


def test_semantic_refresh_rebinds_exact_mode_atomically_and_rejects_stale_delta(code_system):
    binding=selected_mode(code_system)['task_mode_binding']
    assert create(code_system,binding).status=='ok'
    before=PlanStore(code_system[1]).snapshot()
    new=selected_mode(code_system,modes=('CD','VAL'))['task_mode_binding']
    steer=call(code_system,'steer_submit',{'source_event_id':new['source_event_id'],'source_cursor':new['source_cursor'],
        'expected_revision':1,'intent':'semantic','rationale':'Add validation to the pending task.','affected_task_ids':['code-0']})
    assert steer.status=='ok',steer.error
    args={'plan_id':before.plan_id,'title':'Rebound task','expected_revision':1,'expected_document_digest':before.document_digest,
        'steer_request_id':steer.result['request_id'],'tasks':[task(code_system,new).model_dump(mode='json')]}
    bad=json.loads(json.dumps(args));bad['tasks'][0]['mode_binding']['classification_digest']='0'*64
    assert call(code_system,'plan_refresh',bad).error.code=='TASK_MODE_SOURCE_MISMATCH'
    assert PlanStore(code_system[1]).snapshot().document_digest==before.document_digest
    result=call(code_system,'plan_refresh',args)
    assert result.status=='ok',result.error
    current=PlanStore(code_system[1]).snapshot()
    assert current.revision==2 and current.tasks[0].definition.mode_binding.classification_digest==new['classification_digest']
    assert current.tasks[0].contract_digest!=before.tasks[0].contract_digest
    stale=call(code_system,'delta_enter',{'task_id':'code-0','plan_revision':1,'contract_digest':before.tasks[0].contract_digest,
        'action':'code_index','arguments':{'paths':['.']}},expected_revision=1)
    assert stale.error.code=='STALE_PLAN_REVISION'


def test_semantic_steer_before_verification_prevents_bound_task_completion(code_system,monkeypatch):
    binding=selected_mode(code_system)['task_mode_binding']
    assert create(code_system,binding).status=='ok'
    original=code_system[0].delta_exit.finish
    def steer_at_exit(*args,**kwargs):
        source=call(code_system,'lineage_record',{'kind':'prompt','payload':{'text':'Change the remaining source scope.'}})
        steer=call(code_system,'steer_submit',{'source_event_id':source.result['event_id'],'source_cursor':source.result['cursor'],
            'expected_revision':1,'intent':'semantic','rationale':'Change the pending scope.','affected_task_ids':['code-0']})
        assert steer.status=='ok',steer.error
        return original(*args,**kwargs)
    monkeypatch.setattr(code_system[0].delta_exit,'finish',steer_at_exit)
    row=finish(code_system,enter(code_system))
    assert row['state']=='blocked' and row['error_code']=='JOB_CHECKPOINT_REQUIRED',row
    assert PlanStore(code_system[1]).snapshot().revision==1


def test_current_policy_loss_at_verification_cannot_issue_a_success_receipt(code_system,monkeypatch):
    from evidence_lane_plugin.flash_authority import SessionFlashAuthority
    binding=selected_mode(code_system)['task_mode_binding']
    assert create(code_system,binding).status=='ok'
    original=code_system[0].delta_exit.finish
    def unavailable(*args,**kwargs):
        raise LaneError('SESSION_FLASH_REGISTRY_CHANGED','Injected changed locked policy.')
    def verify(*args,**kwargs):
        monkeypatch.setattr(SessionFlashAuthority,'verify',unavailable)
        return original(*args,**kwargs)
    monkeypatch.setattr(code_system[0].delta_exit,'finish',verify)
    row=finish(code_system,enter(code_system))
    assert row['state']=='blocked' and row['error_code']=='SESSION_FLASH_REGISTRY_CHANGED',row
    with code_system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM delta_exits').fetchone()[0]==0
