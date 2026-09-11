"""Retained original ENV/UOP behavior through current owning consumers."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.capture_routing import HookEnvelope
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.flash_authority import SessionFlashAuthority
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.mode_governance import (
    ENV_UOP_EXECUTION_BUDGET_SCHEMA,
    ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
    bind_env_uop_action_policy,
    load_env_uop_runtime_authority,
    route_env_uop_operation,
    validate_env_uop_external_secret_reference,
    validate_mode_governance_selection,
)
from evidence_lane_plugin.operating_modes import MODE_DEFINITIONS, classify_operating_modes
from evidence_lane_plugin.plan_runtime import PlanRead, PlanStore
from evidence_lane_plugin.prompt_index import PromptIndex
from evidence_lane_plugin.sdk import ActionRequest

ROOT=Path(__file__).resolve().parents[1]/'plugins/evidence-lane-plugin'


@pytest.fixture
def selected(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'example.txt').write_text('source evidence')
    with Engine(tmp_path/'runtime') as engine:
        record=engine.directory.register(tmp_path/'state',source_root=source,create=True,read_only=False)
        project=engine.directory.open(record['project_id'],write=True)
        _,client=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id,permissions=['read','write'])]))
        yield engine,project,client


def call(selected,action,arguments=None):
    engine,project,client=selected
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action,project_id=project.project_id,arguments=arguments or {}),client)


def captured_prompt(selected,text='Analyze the source evidence.',*,event_id=None):
    engine,_,_=selected
    assert call(selected,'capture_bind',{'reported_session_id':'reported-fixture'}).status=='ok'
    envelope=HookEnvelope(event_id=event_id or str(uuid4()),event={'hook_event_name':'UserPromptSubmit',
        'session_id':'reported-fixture','turn_id':'turn-1','prompt':text})
    return engine.capture.capture(envelope),envelope


def test_full_policy_keeps_current_codex_action_workflow_and_project_class_families():
    runtime=load_env_uop_runtime_authority()
    assert len(runtime['action_policies'])==297
    assert len(runtime['work_policies'])==16
    assert len(runtime['env']['env_project_class_policy_v4'])==7
    assert len(runtime['env']['env_codex_workflow_stage_v4'])==13
    assert {'SOURCE_CURRENTNESS','PLAN_TASK','TOOL_READINESS','HOST_PLAN_PROJECTION','VERIFICATION'} <= set(runtime['required_gates'])
    assert not runtime['project_payload_accessed'] and not runtime['action_effect_executed']
    assert not runtime['legacy_translation_layer']


def test_retained_ordered_mode_intersections_use_real_authority_and_sector_owners():
    result=classify_operating_modes('Analyze, plan, implement and validate.',explicit_modes=['AL','PL','CD','VAL'],code_lane='local_code')
    assert result['mode_intersection']=='AL+PL+CD+VAL'
    assert result['canonical_lanes']==['chat_lineage','sources','receipts','plan','local_code','artifacts']
    assert not {'mode','analysis','discussion','brain_loader','sqlite_brain','project_engulf'} & set(result['canonical_lanes'])
    assert [row['mode_id'] for row in result['mode_governance']['contracts']]==['AL','PL','CD','VAL']
    assert not result['mode_governance']['selection_authorizes_work']


@pytest.mark.parametrize('mode',[row['id'] for row in MODE_DEFINITIONS if row['id']!='X'])
def test_each_retained_mode_has_its_own_current_loop_action_classes_and_gates(mode):
    result=classify_operating_modes('Select the exact operating mode.',explicit_modes=[mode],code_lane='local_code')
    contract=result['mode_governance']['contracts'][0]
    assert contract['recursive_loop'] and contract['workflow_order'] and contract['exit_write_target']
    assert contract['action_classes'] and contract['required_gates']
    assert not contract['effect_executed'] and not contract['selection_authorizes_work']
    assert not contract['legacy_translation_layer']


def test_mode_inference_is_labeled_and_no_default_mode_is_invented():
    result=classify_operating_modes('Research prior art, then build code and test it.',explicit_modes=None,code_lane='github_code')
    assert [row['id'] for row in result['selected_modes']]==['RS','CD','VAL']
    assert result['mode_governance']['selection_source']=='PROMPT_INFERENCE'
    with pytest.raises(LaneError) as error:
        classify_operating_modes('Do the thing.',explicit_modes=None,code_lane='local_code')
    assert error.value.code=='MODE_SELECTION_REQUIRED'


def test_custom_mode_keeps_explicit_dependency_schema():
    custom={'name':'Evidence comparison','brief':'Compare source code with research evidence.','lanes':['local_code','research']}
    with pytest.raises(LaneError) as error:
        classify_operating_modes('Evidence comparison',explicit_modes=['Evidence comparison'],code_lane='local_code',custom_modes=[custom])
    assert error.value.code=='CUSTOM_MODE_DEPENDENCY_POLICY_REQUIRED'
    custom['dependency_policy']={'requires':['local_code','research'],'on_missing':'BLOCK'}
    result=classify_operating_modes('Evidence comparison',explicit_modes=['Evidence comparison'],code_lane='local_code',custom_modes=[custom])
    assert result['mode_governance']['contracts'][0]['dependency_policy']==custom['dependency_policy']


def test_typed_action_policy_and_exact_lane_tool_budgets_are_preserved():
    result=classify_operating_modes('Code correction.',explicit_modes=['CD'],code_lane='local_code')['mode_governance']
    budget={'schema':ENV_UOP_EXECUTION_BUDGET_SCHEMA,'lane_units':{'local_code':2},'tool_invocations':{'Git':1},
        'max_total_lane_units':2,'max_total_tool_invocations':1}
    kwargs={'mode_id':'CD','action_name':'code_index',
        'lane_id':'local_code','tool_id':'Git','execution_budget':budget,'lane_units':2,'tool_invocations':1}
    receipt=route_env_uop_operation(result,**kwargs)
    assert not receipt['effect_executed'] and not receipt['budget_consumed']
    assert receipt['action_policy']['action_name']=='code_index'
    with pytest.raises(LaneError) as error:
        route_env_uop_operation(result,**{**kwargs,'lane_units':3})
    assert error.value.code=='ENV_UOP_ROUTE_BUDGET_EXCEEDED'
    with pytest.raises(LaneError) as error:
        bind_env_uop_action_policy('removed_action')
    assert error.value.code=='ENV_UOP_ACTION_UNKNOWN'


@pytest.mark.parametrize('storage',['PROMPT','SQLITE','CHAT_LINEAGE','LINEAGE','ASSET','TEST'])
def test_retained_secret_reference_rejects_internal_storage(storage):
    with pytest.raises(LaneError) as error:
        validate_env_uop_external_secret_reference({'schema':ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
            'provider_id':'fixture-provider','reference_id':'opaque-handle','storage_target':storage,'outcome':'RESOLVED'})
    assert error.value.code=='ENV_UOP_SECRET_STORAGE_FORBIDDEN'


def test_sdk_mode_and_policy_retrieval_use_real_locked_consumers_without_plan_mutation(selected):
    engine,project,_=selected
    before=PlanStore(project).snapshot(PlanRead()).model_dump(mode='json')
    result=call(selected,'mode_classify',{'request':'Inspect and plan the correction.','explicit_modes':['AL','PL']})
    assert result.status=='ok',result
    assert result.tool_execution['env_uop']['owner_skill']=='classify-project-work'
    assert result.tool_execution['env_uop']['data_touch_allowed']
    queried=call(selected,'env_uop_inspect',{'role':'uop','query':'source currentness','limit':4})
    assert queried.status=='ok' and queried.result['matches'],queried
    assert queried.result['query_mode']=='owning_sqlite_fts5_bm25'
    assert before==PlanStore(project).snapshot(PlanRead()).model_dump(mode='json')
    names={row['name'] for row in engine.registry.schemas()}
    assert {'mode_classify','task_classify','prompt_index_status'}<=names
    assert 'formula_engine_run' not in names and 'pv_fuse' not in names


def test_prompt_capture_and_entry_are_coherent_idempotent_and_separate_from_plan(selected):
    engine,project,client=selected
    before=PlanStore(project).snapshot(PlanRead()).model_dump(mode='json')
    source,envelope=captured_prompt(selected)
    page=PromptIndex(project).status(client_id=client.client_id,reported_session_id='reported-fixture')
    assert len(page['entries'])==1
    entry=page['entries'][0]
    assert entry['source_cursor']==source.cursor and entry['prompt_index']==1
    assert entry['intent']=='unclassified' and not entry['effects_authorized']
    assert engine.capture.capture(envelope).duplicate
    assert len(PromptIndex(project).status(client_id=client.client_id)['entries'])==1
    exact=PromptIndex(project).resolve(client_id=client.client_id,reported_session_id='reported-fixture',reference='PROMPT 1')
    assert exact['entry']['entry_digest']==entry['entry_digest']
    assert not exact['restoration_authorized']
    assert before==PlanStore(project).snapshot(PlanRead()).model_dump(mode='json')


def test_prompt_entry_failure_rolls_back_source_capture_and_publication(selected,monkeypatch):
    _,project,_=selected
    assert call(selected,'capture_bind',{'reported_session_id':'reported-fixture'}).status=='ok'
    before=project.lane_catalog()
    with project.lane('receipts').connection(read_only=True) as connection:
        before_receipts={row[0] for row in connection.execute('SELECT receipt_id FROM receipts')}
    def fail(*args,**kwargs):raise LaneError('FIXTURE_ENTRY_FAILURE','Injected after source append.')
    monkeypatch.setattr(PromptIndex,'record_entry',fail)
    with pytest.raises(LaneError) as error:
        captured_prompt(selected)
    assert error.value.code=='FIXTURE_ENTRY_FAILURE'
    with project.lane('chat_lineage').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='lineage_events'").fetchone()
    # Writer acquisition commits its own Receipts head. Every other lane must
    # retain its exact publication, and no prompt success receipt may survive.
    assert [r for r in project.lane_catalog() if r['lane_id']!='receipts']==[r for r in before if r['lane_id']!='receipts']
    with project.lane('receipts').connection(read_only=True) as connection:
        added=[row['kind'] for row in connection.execute('SELECT receipt_id,kind FROM receipts') if row['receipt_id'] not in before_receipts]
    assert added==['writer_acquired']


def test_explicit_classification_binds_source_and_leaves_plan_unchanged(selected):
    _,project,_=selected
    source,_=captured_prompt(selected)
    before=PlanStore(project).snapshot(PlanRead()).model_dump(mode='json')
    args={'classification_id':str(uuid4()),'source_event_id':source.event_id,'source_cursor':source.cursor,
        'intent':'informational','focus':'Inspect the current Plan.','workflow':'plan','next_action':'plan_read','lanes':['plan'],
        'explicit_modes':['PL']}
    result=call(selected,'task_classify',args)
    assert result.status=='ok',result
    assert not result.result['plan_changed']
    assert call(selected,'task_classify',args).result==result.result
    bad=call(selected,'task_classify',{**args,'classification_id':str(uuid4()),'source_cursor':'0'*64})
    assert bad.error.code=='PROMPT_SOURCE_MISMATCH'
    bad=call(selected,'task_classify',{**args,'classification_id':str(uuid4()),'next_action':'plan_refresh'})
    assert bad.error.code=='PROMPT_INFORMATIONAL_ROUTE_INVALID'
    assert before==PlanStore(project).snapshot(PlanRead()).model_dump(mode='json')


def test_policy_source_tampering_is_rejected_before_admission(selected,tmp_path):
    engine,_,_=selected
    manifest=json.loads((ROOT/'env/SESSION_FLASH_MANIFEST.json').read_bytes())
    for member in [*manifest['members'],{'path':'env/SESSION_FLASH_MANIFEST.json'}]:
        target=tmp_path/'policy'/member['path'];target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/member['path']).read_bytes())
    authority=SessionFlashAuthority(asset_root=tmp_path/'policy')
    authority.verify(registry=engine.registry)
    target=tmp_path/'policy/env/codex-environment-policy.v4.json'
    target.write_bytes(target.read_bytes()+b' ')
    engine.registry.control_plane.flash=authority
    response=call(selected,'workflow_catalog')
    assert response.status=='error' and response.error.code=='SESSION_FLASH_MEMBER_HASH_MISMATCH'


def test_mode_retains_work_classification_required_gates_and_real_conditional_routes():
    result=classify_operating_modes('Implement a code correction.',explicit_modes=['CD'],code_lane='local_code')
    contract=result['mode_governance']['contracts'][0]
    assert contract['work_classification']=={
        'work_id':'CD','work_name':'code','action_classes':['CODE'],'source':'env_work_policy_v4'}
    assert {'SOURCE_CURRENTNESS','PLAN_TASK','TOOL_READINESS','VERIFICATION'} <= set(contract['required_gates'])
    assert {r['project_class'] for r in contract['project_class_applicability']['class_policies']}=={'CODE_REPOSITORY','MIXED_PROJECT'}
    route=next(r for r in contract['conditional_toolchain']['lane_bindings'] if r['lane_id']=='local_code')
    assert 'code_index' in route['action_names']
    assert 'Git' in route['ordered_tools'] and 'CODE' in route['action_classes']
    assert not contract['conditional_toolchain']['all_tools_run_each_turn']


@pytest.mark.parametrize('change',['lanes','effect','authorize','class_policy'])
def test_rehashing_a_modified_mode_does_not_change_its_locked_effects(change):
    from evidence_lane_plugin.plan_runtime import content_digest
    result=classify_operating_modes('Implement.',explicit_modes=['CD'],code_lane='local_code')['mode_governance']
    contract=result['contracts'][0]
    if change=='lanes':contract['canonical_lanes']=['research']
    elif change=='effect':contract['effect_executed']=True
    elif change=='authorize':contract['selection_authorizes_work']=True
    else:contract['project_class_applicability']['class_policies'][0]['ci_strategy']='skip all checks'
    contract['contract_sha256']=content_digest({k:v for k,v in contract.items() if k!='contract_sha256'})
    result['selection_sha256']=content_digest({k:v for k,v in result.items() if k!='selection_sha256'})
    with pytest.raises(LaneError) as error:
        validate_mode_governance_selection(result)
    assert error.value.code in {'ENV_UOP_SELECTION_CHANGED','ENV_UOP_MODE_LANE_INVALID'}


@pytest.mark.parametrize(('name','content','project_type','project_class','mode'),[
    ('rows.csv','name,value\na,1\n','DATA','DATA_PROJECT','XL'),
    ('picture.svg','<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>','MEDIA','MEDIA_PROJECT','OP'),
    ('app.py','answer = 42\n','CODE','CODE_REPOSITORY','CD'),
])
def test_recipe_applies_class_policy_and_keeps_mode_distinct(selected,name,content,project_type,project_class,mode):
    _,project,_=selected
    path=project.source_root/name;path.write_text(content)
    registered=call(selected,'source_register',{'sources':[str(path)]})
    assert registered.status=='ok',registered
    batch=registered.result['result']['source_authority']['batch_id']
    before=project.pv_head()
    result=call(selected,'project_recipe',{'batch_id':batch,'requested_outcome':'Inspect this selected source.', 'explicit_modes':[mode]})
    assert result.status=='ok',result
    assert result.result['project_type']==project_type
    assert result.result['project_class_policy']['project_class']==project_class
    assert not result.result['project_class_policy']['plugin_maintainer_ci_imposed']
    assert result.result['mode_classification']['selected_modes'][0]['id']==mode
    assert result.result['recipe_and_mode_are_distinct']
    assert project.pv_head()==before


def test_sdk_lineage_records_prompt_entry_and_rejects_partial_publication(selected,monkeypatch):
    _,project,client=selected
    args={'event_id':str(uuid4()),'kind':'prompt','payload':{'text':'Inspect current source.'},'reported_session_id':'sdk-fixture'}
    first=call(selected,'lineage_record',args)
    assert first.status=='ok',first
    assert call(selected,'lineage_record',args).result['duplicate']
    assert len(PromptIndex(project).status(client_id=client.client_id)['entries'])==1
    def fail(*args,**kwargs):raise LaneError('FIXTURE_ENTRY_FAILURE','Injected after SDK source append.')
    monkeypatch.setattr(PromptIndex,'record_entry',fail)
    failed_id=str(uuid4())
    failed=call(selected,'lineage_record',{**args,'event_id':failed_id})
    assert failed.error.code=='FIXTURE_ENTRY_FAILURE'
    with project.lane('chat_lineage').connection(read_only=True) as connection:
        assert not connection.execute('SELECT 1 FROM lineage_events WHERE event_id=?',(failed_id,)).fetchone()


@pytest.mark.parametrize('inject_failure',[False,True])
def test_sdk_steer_and_classification_commit_together_without_plan_advance(selected,monkeypatch,inject_failure):
    from evidence_lane_plugin.steering import Steering
    _,project,client=selected
    created=call(selected,'plan_create',{'title':'Fixture Plan','tasks':[{'task_id':'one','title':'One','requested_outcome':'Fixture outcome'}]})
    assert created.status=='ok',created
    source=call(selected,'lineage_record',{'kind':'prompt','payload':{'text':'Change the next implementation task.'}})
    assert source.status=='ok',source
    before=PlanStore(project).snapshot()
    if inject_failure:
        def fail(*args,**kwargs):raise LaneError('FIXTURE_CLASSIFY_FAILURE','Injected after steer queue append.')
        monkeypatch.setattr(PromptIndex,'classify',fail)
    args={'request_id':str(uuid4()),'source_event_id':source.result['event_id'],'source_cursor':source.result['cursor'],
          'expected_revision':1,'intent':'semantic','rationale':'Change the selected task.','affected_task_ids':['one']}
    result=call(selected,'steer_submit',args)
    if inject_failure:
        assert result.error.code=='FIXTURE_CLASSIFY_FAILURE'
        assert Steering(project).pending()==[]
    else:
        assert result.status=='ok',result
        assert call(selected,'steer_submit',args).result['duplicate']
        entry=PromptIndex(project).status(client_id=client.client_id)['entries'][0]
        assert entry['classification']['classification_id']==args['request_id']
        assert entry['classification']['next_action']=='plan_refresh'
        assert not entry['classification']['plan_changed']
    assert PlanStore(project).snapshot()==before


@pytest.mark.parametrize('kind',['ordinary_turn','delta_append','session_exit','state_travel','goal_completion'])
def test_exit_boundary_reads_exact_source_without_converting_claims_to_native_proof(selected,kind):
    _,project,_=selected
    source=call(selected,'lineage_record',{'kind':'assistant','payload':{'text':'Goal complete. State Travel finished. PASS.'}})
    assert source.status=='ok',source
    before=project.pv_head()
    result=call(selected,'session_exit_boundary',{'kind':kind,'source_event_id':source.result['event_id'],'source_cursor':source.result['cursor']})
    assert result.status=='ok',result
    assert result.result['source_reference']['payload_digest']==source.result['payload_digest']
    assert not result.result['source_reference']['native_action_proven']
    assert not result.result['terminal_exit_allowed'] and not result.result['terminal_receipt_emitted']
    assert result.result['evidence_state']==('native_evidence_unavailable' if kind in {'state_travel','goal_completion'} else 'nonterminal_boundary')
    assert project.pv_head()==before
    wrong=call(selected,'session_exit_boundary',{'kind':kind,'source_event_id':source.result['event_id'],'source_cursor':'0'*64})
    assert wrong.error.code=='EXIT_SOURCE_MISMATCH'


def test_prompt_indices_cannot_page_across_different_session_sequences(selected):
    _,project,client=selected
    for session in ['session-a','session-b']:
        assert call(selected,'lineage_record',{'kind':'prompt','payload':{'text':'Inspect.'},'reported_session_id':session}).status=='ok'
    with pytest.raises(LaneError) as error:
        PromptIndex(project).status(client_id=client.client_id,after_index=1)
    assert error.value.code=='PROMPT_INDEX_SESSION_REQUIRED'
    assert len(PromptIndex(project).status(client_id=client.client_id,reported_session_id='session-b')['entries'])==1


def test_accepted_engine_continuation_does_not_claim_native_state_travel(selected):
    engine,project,_=selected
    _,receiver=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id,permissions=['read','write'])]))
    target=(engine,project,receiver)
    assert call(selected,'plan_create',{'title':'Continuity fixture','tasks':[{'task_id':'next','title':'Next','requested_outcome':'Continue selected work.'}]}).status=='ok'
    task=PlanStore(project).task('next',expected_revision=1)
    source=call(selected,'canon_join',{'label':'Source fixture'})
    destination=call(target,'canon_join',{'label':'Destination fixture'})
    assert source.status==destination.status=='ok'
    offer=call(selected,'continuation_offer',{'participant_id':source.result['participant_id'],
        'destination_participant_id':destination.result['participant_id'],'plan_revision':1,'task_id':'next','contract_digest':task.contract_digest})
    assert offer.status=='ok',offer
    refs={key:offer.result[key] for key in ('continuation_id','continuation_digest')}
    accepted=call(target,'continuation_accept',refs)
    assert accepted.status=='ok',accepted
    before=project.pv_head()
    checked=call(target,'session_exit_boundary',{'kind':'state_travel',**refs})
    assert checked.status=='ok',checked
    assert checked.result['continuation_reference']['state']=='accepted'
    assert checked.result['continuation_reference']['binding_digest']==accepted.result['binding_digest']
    assert not checked.result['continuation_reference']['native_handoff_proven']
    assert not checked.result['terminal_exit_allowed']
    assert project.pv_head()==before


def test_env_uop_policy_and_mode_reads_keep_the_exact_delta_query_plan_revision(selected):
    engine,project,client=selected
    assert call(selected,'plan_create',{'title':'Query fixture','tasks':[{'task_id':'one','title':'One','requested_outcome':'Read source policy.'}]}).status=='ok'
    before=project.pv_head()
    for action,args in [('env_uop_inspect',{'query':'source currentness','limit':2}),
                        ('mode_classify',{'request':'Analyze.','explicit_modes':['AL']})]:
        query=ActionRequest(action='delta_query',project_id=project.project_id,expected_revision=1,
            arguments={'task_id':'one','plan_revision':1,'action':action,'arguments':args})
        result=PublicActionSDKDispatcher(engine).execute(query,client)
        assert result.status=='ok',result
        assert result.result['plan_revision']==1 and not result.result['mutation_performed']
    assert project.pv_head()==before
