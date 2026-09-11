"""Real engine capture, immutable compact references and honest scope failures."""
# ruff: noqa: F811 -- pytest resolves the explicitly imported fixture by name.
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.capture_routing import HookEnvelope
from evidence_lane_plugin.codex_turn_control import TurnControl, _seal_compact_context_size
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hook_contract import context_hook_output
from evidence_lane_plugin.lineage import ChatLineage, LineageRead
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore, TaskDefinition
from evidence_lane_plugin.project_memory import MemoryIngest, MemoryLocator, MemoryReference
from evidence_lane_plugin.storage import json_text
from evidence_lane_plugin.writers import WriterLease

from tests.test_session_v4 import boot, call, connect, resume_args, selected  # noqa: F401


def setup_turn(system, *, count=1, active=0):
    engine, project, _, client = system
    tasks = [TaskDefinition(task_id=f'task-{i}', title=f'Work {i}', requested_outcome='Inspect source bytes.',
        acceptance_checks=['Verify selected source bytes.'], dependencies=[]) for i in range(count)]
    created = call(engine, project, client, 'plan_create', {'title':'Operating context',
        'tasks':[task.model_dump(mode='json') for task in tasks]})
    assert created.status == 'ok', created.error
    with WriterLease(project, engine.instance_id) as lease:
        PlanStore(project).transition(f'task-{active}', 'active', lease, expected_revision=1, actor_id=client.client_id)
    result = boot(engine, project, client)
    assert result.status == 'ok', result.error
    return result.result


def capture(system, name, **extra):
    engine = system[0]
    event = {'hook_event_name':name, 'session_id':'host-reported-session', 'turn_id':'turn-1', **extra}
    envelope = HookEnvelope(event_id=str(uuid4()), event=event)
    return engine.capture.capture(envelope), envelope


def read(system):
    response = call(system[0], system[1], system[3], 'session_context')
    assert response.status == 'ok', response.error
    return response.result['operating_context']


def body(system, event_digest):
    with system[1].lane('chat_lineage').connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM turn_events WHERE digest=?',(event_digest,)).fetchone()
    return TurnControl(system[0], system[1])._row(row)


def test_prompt_tools_and_response_bind_one_exact_engine_turn(selected):
    setup_turn(selected)
    prompt, _ = capture(selected, 'UserPromptSubmit', prompt='Visible bounded input.')
    prepared = body(selected, prompt.turn_control['event_digest'])
    assert prepared['context']['prompt_entry']['source_event_id'] == prompt.event_id
    assert prepared['context']['context_ready']
    for name in ('PreToolUse','PostToolUse','Stop'):
        result, _ = capture(selected, name, tool_name='Bash',tool_use_id='one',tool_input={'command':'example'},
            tool_response={'visible':'result'},last_assistant_message='Visible response.')
        assert result.turn_control['prepared_turn']['event_digest'] == prompt.turn_control['event_digest']
        assert not result.turn_control['gaps']
        assert result.turn_control['terminal_exit'] is False
    assert PlanStore(selected[1]).task('task-0', expected_revision=1).state == 'active'
    verified = TurnControl(selected[0], selected[1]).verify_history()
    assert verified['events_verified'] == 4


def test_compact_pair_and_reentry_return_bounded_separate_references_read_only(selected):
    setup_turn(selected, count=20, active=15)
    capture(selected, 'UserPromptSubmit', prompt='DO NOT replay this raw prompt after compaction.')
    pre, envelope = capture(selected, 'PreCompact', trigger='manual')
    duplicate = selected[0].capture.capture(envelope)
    assert duplicate.duplicate and duplicate.turn_control == pre.turn_control
    post, _ = capture(selected, 'PostCompact', trigger='manual')
    assert post.turn_control['compact']['state'] == 'compatible_engine_context'
    start, _ = capture(selected, 'SessionStart', source='compact')
    assert start.turn_control['compact']['precompact_event_digest'] == pre.turn_control['event_digest']
    before = selected[1].pv_head()
    context = read(selected)
    assert selected[1].pv_head() == before
    current = context['current']
    assert current['serialized_bytes'] == len(json_text(current).encode()) <= 8192
    assert current['plan']['partial_plan_window'] is False
    assert current['plan']['full_plan_rows_in_hook_context'] is False
    assert current['plan']['active_task_id'] == 'task-15'
    assert current['plan']['active_task']['task_id'] == 'task-15'
    assert current['plan']['host_plan_projection']['row_count'] == 20
    assert current['plan']['host_plan_projection']['full_list'] is True
    assert {'canon','learning','memory','plan','chat_lineage','sources','receipts','universe'} <= set(current['lane_heads'])
    assert context['compact']['postcompact_event_digest'] == post.turn_control['event_digest']
    assert context['compact']['state'] == 'compatible_engine_context'
    assert 'DO NOT replay' not in json.dumps(context)
    assert not current['execution_authorized'] and not current['native_goal_completed']
    assert current['native_task_attestation'] == 'not_provided'


def test_missing_turn_or_engine_session_keeps_capture_and_reports_gap(selected):
    engine, project, _, client = selected
    response = call(engine, project, client, 'capture_bind', {'reported_session_id':'host-reported-session'})
    assert response.status == 'ok'
    result, _ = capture(selected, 'PreCompact', trigger='auto')
    assert result.turn_control['gaps'] == ['active_engine_session_missing']
    assert result.turn_control['context_digest'] is None
    assert read(selected)['current']['context_ready'] is False


def test_missing_prepare_or_response_cannot_be_a_completed_turn(selected):
    setup_turn(selected)
    stopped, _ = capture(selected, 'Stop')
    assert set(stopped.turn_control['gaps']) == {'visible_turn_prepare_missing','visible_response_missing'}
    capture(selected, 'UserPromptSubmit', prompt='Prepare this turn.')
    other, _ = capture(selected, 'Stop', turn_id='other-turn', last_assistant_message='A different response')
    assert other.turn_control['prepared_turn'] is None
    assert other.turn_control['gaps'] == ['visible_turn_prepare_missing']


def test_missing_post_or_pre_and_repeated_post_are_explicit(selected):
    setup_turn(selected)
    result, _ = capture(selected, 'PostCompact')
    assert result.turn_control['gaps'] == ['matching_precompact_missing']
    capture(selected, 'PreCompact')
    result, _ = capture(selected, 'SessionStart',source='compact')
    assert result.turn_control['gaps'] == ['matching_postcompact_missing']
    capture(selected, 'PostCompact')
    repeated, _ = capture(selected, 'PostCompact')
    assert repeated.turn_control['gaps'] == ['matching_precompact_missing']


def test_plan_state_change_requires_fresh_context_and_cannot_resume_sealed_active_row(selected):
    setup_turn(selected)
    pre, _ = capture(selected, 'PreCompact')
    engine, project, _, client = selected
    with WriterLease(project, engine.instance_id) as lease:
        PlanStore(project).transition('task-0','blocked',lease,expected_revision=1,actor_id=client.client_id)
    post, _ = capture(selected, 'PostCompact')
    compact = post.turn_control['compact']
    assert compact['state'] == 'context_refresh_required'
    assert {'plan_changed','active_plan_task_missing'} <= set(compact['reasons'])
    assert compact['precompact_event_digest'] == pre.turn_control['event_digest']
    assert not compact['execution_authorized']


def test_resume_cannot_reuse_previous_engine_session_generation(selected):
    first = setup_turn(selected)
    capture(selected, 'PreCompact')
    engine, project, _, client = selected
    resumed = call(engine, project, client, 'session_resume', resume_args(project, first))
    assert resumed.status == 'ok', resumed.error
    result, _ = capture(selected, 'PostCompact', session_id='resumed-host-session')
    assert result.turn_control['compact'] is None
    assert result.turn_control['gaps'] == ['matching_precompact_missing']


def test_context_failure_rolls_back_source_prompt_and_turn_together(selected,monkeypatch):
    setup_turn(selected)
    def fail(*args,**kwargs):
        raise LaneError('INJECTED_CONTEXT_FAILURE','Injected after visible source preparation.')
    monkeypatch.setattr(TurnControl,'_snapshot',fail)
    with pytest.raises(LaneError, match='Injected'):
        capture(selected,'UserPromptSubmit',prompt='This provisional source must not commit.')
    assert ChatLineage(selected[1]).read(LineageRead()).total_events == 0
    with selected[1].lane('chat_lineage').connection(read_only=True) as connection:
        for table in ('prompt_entries','turn_events'):
            exists=connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?",(table,)).fetchone()
            assert not exists or connection.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0


def test_corrupt_compact_object_rejected_without_new_completion(selected):
    setup_turn(selected)
    pre, _ = capture(selected,'PreCompact')
    store = selected[1].lane('chat_lineage')
    with store.connection(read_only=True) as connection:
        digest=connection.execute('SELECT body_digest FROM turn_events WHERE digest=?',(pre.turn_control['event_digest'],)).fetchone()[0]
    path=store.object_path(digest)
    original=path.read_bytes()
    try:
        path.write_bytes(b'{}')
        with pytest.raises(LaneError):
            capture(selected,'PostCompact')
    finally:
        path.write_bytes(original)
    assert ChatLineage(selected[1]).read(LineageRead()).total_events == 1


def test_context_size_is_measured_and_excess_is_rejected():
    with pytest.raises(LaneError) as error:
        _seal_compact_context_size({'schema':'evidence-lane.turn-context.v4','too_large':'x'*8192})
    assert error.value.code == 'TURN_CONTEXT_BYTE_BUDGET'


def test_compact_turn_mismatch_is_not_paired(selected):
    setup_turn(selected)
    capture(selected,'PreCompact',turn_id='turn-one')
    post,_=capture(selected,'PostCompact',turn_id='turn-two')
    assert post.turn_control['compact'] is None
    assert post.turn_control['gaps'] == ['compact_turn_binding_mismatch']


def test_context_output_rejects_different_hook_and_never_returns_control_fields(selected):
    setup_turn(selected)
    prompt,envelope=capture(selected,'UserPromptSubmit',prompt='user raw text must not enter developer context')
    delivery={'captured':True,'result':prompt.model_dump(mode='json')}
    output=context_hook_output(envelope,delivery)
    assert set(output) == {'hookSpecificOutput'}
    assert set(output['hookSpecificOutput']) == {'hookEventName','additionalContext'}
    assert 'user raw text' not in output['hookSpecificOutput']['additionalContext']
    wrong=envelope.model_copy(update={'event_id':str(uuid4())})
    with pytest.raises(LaneError) as error:
        context_hook_output(wrong,delivery)
    assert error.value.code == 'HOOK_CONTEXT_BINDING'
    pre,_=capture(selected,'PreCompact')
    assert pre.turn_control['bounded_context'] is None


def test_actual_packaged_handler_returns_context_only_on_documented_events(selected):
    setup_turn(selected)
    engine=selected[0]
    environment=os.environ.copy()
    environment['EVIDENCE_LANE_RUNTIME_ROOT']=str(engine.root)
    path=Path(__file__).resolve().parents[1]/'plugins/evidence-lane-plugin/hooks/invoke_hook.py'
    with LocalEndpoint(engine):
        for name,extra in [('UserPromptSubmit',{'prompt':'Exact visible input'}),('PreCompact',{'trigger':'auto'}),
                ('PostCompact',{'trigger':'auto'}),('SessionStart',{'source':'compact'})]:
            event={'hook_event_name':name,'session_id':'host-reported-session','turn_id':'turn-1',**extra}
            process=subprocess.run([sys.executable,str(path),'--event',name],input=json.dumps(event),env=environment,
                capture_output=True,text=True,timeout=10,check=False)
            assert process.returncode == 0, process.stdout+process.stderr
            output=json.loads(process.stdout)
            if name in {'PreCompact','PostCompact'}:
                assert output == {}
            else:
                assert output['hookSpecificOutput']['hookEventName'] == name
                text=output['hookSpecificOutput']['additionalContext']
                assert len(text.encode()) <= 12000
                assert 'Exact visible input' not in text
                assert 'native_task_attestation' in text
                if name=='SessionStart':
                    assert 'compatible_engine_context' in text


def test_plan_changed_during_turn_is_visible_at_response_boundary(selected):
    setup_turn(selected)
    capture(selected,'UserPromptSubmit',prompt='Prepare this work.')
    engine,project,_,client=selected
    with WriterLease(project,engine.instance_id) as lease:
        PlanStore(project).transition('task-0','blocked',lease,expected_revision=1,actor_id=client.client_id)
    response,_=capture(selected,'Stop',last_assistant_message='Visible answer at changed work boundary.')
    validation=response.turn_control['prepared_turn']['validation']
    assert validation['state']=='context_refresh_required'
    assert 'plan_changed' in validation['reasons']
    assert response.turn_control['terminal_exit'] is False


def memory_locators(system, *, label='MEMORY CONTENT MUST NOT BE INJECTED', count=6):
    engine,project,_,client=system
    task=PlanStore(project).task('task-0',expected_revision=1)
    locators=[MemoryLocator(reference=MemoryReference(kind='plan_task',key='task-0',revision=1,
        digest=task.contract_digest),label=f'{label} {index}') for index in range(count)]
    request=MemoryIngest(locators=locators)
    response=call(engine,project,client,'memory_ingest',request.model_dump(mode='json'))
    assert response.status=='ok',response.error


def test_compact_seals_four_memory_locators_in_own_lane_and_rehydrates_exact_checkpoint(selected):
    setup_turn(selected)
    memory_locators(selected)
    pre,_=capture(selected,'PreCompact')
    checkpoint=body(selected,pre.turn_control['event_digest'])['context']['memory_checkpoint']
    assert len(checkpoint['locator_ids'])==4
    with selected[1].lane('memory').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM memory_checkpoints WHERE checkpoint_digest=?',
            (checkpoint['checkpoint_digest'],)).fetchone()[0]==1
    with selected[1].lane('chat_lineage').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='memory_checkpoints'").fetchone()
    post,_=capture(selected,'PostCompact')
    assert post.turn_control['compact']['state']=='compatible_engine_context'
    rehydrated=post.turn_control['compact']['memory_rehydration']
    assert rehydrated['checkpoint_digest']==checkpoint['checkpoint_digest']
    assert rehydrated['locator_ids']==checkpoint['locator_ids']
    assert not rehydrated['memory_head_changed']
    start,envelope=capture(selected,'SessionStart',source='compact')
    output=context_hook_output(envelope,{'captured':True,'result':start.model_dump(mode='json')})
    assert 'MEMORY CONTENT MUST NOT BE INJECTED' not in json.dumps(output)
    memory_locators(selected,label='A later verified Memory reference',count=1)
    changed=read(selected)['compact']
    assert changed['state']=='context_refresh_required'
    assert 'memory_checkpoint_changed' in changed['reasons']


def test_failed_compact_rolls_back_memory_checkpoint_and_visible_source(selected,monkeypatch):
    setup_turn(selected)
    memory_locators(selected,count=1)
    original=TurnControl._seal_compact_project_memory
    def fail(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise LaneError('INJECTED_MEMORY_CONTEXT_FAILURE','Failure after the provisional memory checkpoint.')
    monkeypatch.setattr(TurnControl,'_seal_compact_project_memory',fail)
    with pytest.raises(LaneError):
        capture(selected,'PreCompact')
    with selected[1].lane('memory').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM memory_checkpoints').fetchone()[0]==0
    assert ChatLineage(selected[1]).read(LineageRead()).total_events==0


def test_interrupt_signal_and_capture_commit_together(selected,monkeypatch):
    from evidence_lane_plugin.steering import Steering
    setup_turn(selected)
    original=Steering.record_interrupt
    def fail(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise LaneError('INJECTED_INTERRUPT_FAILURE','Failure after provisional pause.')
    monkeypatch.setattr(Steering,'record_interrupt',fail)
    with pytest.raises(LaneError):
        capture(selected,'Interrupt')
    assert ChatLineage(selected[1]).read(LineageRead()).total_events==0
    assert not Steering(selected[1]).control()['paused']
    monkeypatch.setattr(Steering,'record_interrupt',original)
    captured,_=capture(selected,'Interrupt')
    assert Steering(selected[1]).control()['source_event_id']==captured.event_id


def test_duplicate_prompt_receipt_does_not_reinject_historical_context(selected):
    setup_turn(selected)
    _,envelope=capture(selected,'UserPromptSubmit',prompt='One exact event.')
    repeated=selected[0].capture.capture(envelope)
    assert repeated.duplicate
    assert context_hook_output(envelope,{'captured':True,'result':repeated.model_dump(mode='json')})=={}
