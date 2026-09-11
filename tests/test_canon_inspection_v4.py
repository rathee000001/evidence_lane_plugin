"""Source-owned inbox and inspector retain bounded read-only Canon semantics."""
# ruff: noqa: F811 -- pytest resolves explicitly imported fixtures by name.
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import (
    CanonDecide,
    CanonExpected,
    CanonInbox,
    CanonInspect,
    CanonSend,
    CanonStore,
)
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import content_digest

from tests.test_canon_v4 import decide, pair, send  # noqa: F401
from tests.test_session_v4 import selected as system  # noqa: F401


def test_inbox_exact_receiver_state_and_visible_events_without_payload_or_writes(pair):
    invoke, sender, receiver, source, target, (_, project, _, _) = pair
    pending, _ = send(pair, payload={'summary':'Payload must stay absent','fields':{'body':'Raw body stays in the envelope'}})
    admitted, _ = send(pair)
    decision, _ = decide(pair, admitted, reason='Receiver chose this exact requirement.')
    outgoing = invoke(receiver, 'task_evidence_send', CanonSend(sender_id=target, receiver_id=source,
        kind='evidence', payload={'summary':'Outgoing evidence'}))
    assert outgoing.status == 'ok', outgoing
    before = project.pv_head(), project.lane_catalog()
    response = invoke(sender, 'task_evidence_inbox', {'receiver_id':target,'states':['admitted']})
    assert response.status == 'ok', response
    page = response.result
    assert project.pv_head() == before[0] and project.lane_catalog() == before[1]
    assert page['packet_count'] == 1 and page['packets'][0]['exchange_id'] == admitted.result['exchange_id']
    assert page['receiver']['participant_id'] == target and page['receiver']['owner_client_id'] == receiver.client_id
    assert page['receiver']['native_task_attestation'] == 'not_provided'
    assert [event['kind'] for event in page['packets'][0]['events']] == ['send','decide']
    event = page['packets'][0]['events'][-1]
    assert event['actor_id'] == receiver.client_id and event['result']['reason'] == decision.result['reason']
    assert event['reason_evidence'] == 'recorded_event'
    assert page['projection_digest'] == content_digest({key:value for key,value in page.items() if key != 'projection_digest'})
    pending_page = invoke(receiver, 'task_evidence_inbox', {'receiver_id':target,'states':['received']}).result
    assert pending_page['packets'][0]['exchange_id'] == pending.result['exchange_id']
    assert 'Raw body stays in the envelope' not in json.dumps(pending_page)
    assert 'Payload must stay absent' not in json.dumps(pending_page)
    assert pending_page['raw_payload_returned'] is False
    wrong = invoke(sender, 'task_evidence_inbox', {'receiver_id':target,'exchange_id':outgoing.result['exchange_id']})
    assert wrong.error.code == 'CANON_INBOX_ROUTE_MISMATCH'


def test_inbox_packet_and_event_cursors_allow_complete_bounded_retrieval(pair):
    invoke, _, receiver, _, target, (_, project, _, _) = pair
    first, _ = send(pair)
    decide(pair, first)
    second, _ = send(pair)
    first_page = invoke(receiver, 'task_evidence_inbox', {'receiver_id':target,'limit':1,'events_per_packet':1}).result
    packet = first_page['packets'][0]
    assert first_page['truncated'] and packet['events_truncated']
    assert packet['events'][0]['kind'] == 'send'
    events = CanonStore(project).inbox(CanonInbox(receiver_id=target, exchange_id=first.result['exchange_id'],
        after_event_sequence=packet['last_event_sequence'], events_per_packet=1))
    assert events.packets[0]['events'][0]['kind'] == 'decide'
    assert events.packets[0]['events_truncated'] is False
    next_page = invoke(receiver, 'task_evidence_inbox', {'receiver_id':target,'after_sequence':first_page['last_sequence'],'limit':1}).result
    assert next_page['packets'][0]['exchange_id'] == second.result['exchange_id'] and not next_page['truncated']
    assert next_page['history'] == first_page['history']


def test_inbox_metadata_preserves_payload_identity_and_supersession(pair):
    invoke, _, receiver, _, target, (_, project, _, _) = pair
    first, _ = send(pair)
    decide(pair, first)
    correction, request = send(pair, kind='correction', supersedes=first.result['exchange_id'])
    decide(pair, correction)
    before = project.pv_head()
    page = invoke(receiver, 'task_evidence_inbox', {'receiver_id':target}).result
    old, current = page['packets']
    assert old['state'] == 'superseded' and old['supersession']['successor_id'] == current['exchange_id']
    assert current['payload_digest'] == content_digest(request.payload.model_dump())
    assert current['supersedes'] == old['exchange_id']
    inspected = invoke(receiver, 'task_evidence_inspect', {})
    assert inspected.status == 'ok', inspected
    assert inspected.result['objects_verified']['supersession_links'] == 1
    assert inspected.result['historical_supersession_links_unavailable'] == 0
    assert project.pv_head() == before


def test_inspector_reports_actual_counts_schema_and_integrity_scope(pair):
    invoke, _, receiver, source, target, (_, project, _, _) = pair
    contract = invoke(receiver, 'task_evidence_expect', CanonExpected(receiver_id=target, contract_key='typed_input',
        sender_ids=[source], kinds=['requirements']))
    assert contract.status == 'ok'
    first, _ = send(pair)
    decide(pair, first, decision='reject')
    before = project.pv_head(), project.lane_catalog()
    result = invoke(receiver, 'task_evidence_inspect', {})
    assert result.status == 'ok', result
    value = result.result
    assert value['initialized'] and value['integrity'] == ['ok'] and value['foreign_key_errors'] == []
    assert value['counts']['canon_participants'] == 2 and value['counts']['canon_exchanges'] == 1
    assert value['objects_verified']['contracts'] == value['objects_verified']['current_contracts'] == 1
    assert value['packet_states'] == {'rejected':1} and value['history']['events_verified'] == 5
    assert [item['version'] for item in value['applied_migrations']] == [1, 2, 3, 4, 5]
    assert value['schema_contract']['lane_id'] == 'canon'
    assert 'cross_project_graph_completeness' in value['unverified_scope']
    assert 'recorded_task_edge_dag' in value['verified_scope'] and 'event_chain' in value['verified_scope']
    assert value['projection_digest'] == content_digest({key:entry for key,entry in value.items() if key != 'projection_digest'})
    assert project.pv_head() == before[0] and project.lane_catalog() == before[1]


def test_inspection_of_fresh_lane_does_not_initialize_it(system):
    _, project, _, _ = system
    owner = CanonStore(project)
    before = project.pv_head(), project.lane_catalog()
    value = owner.inspect(CanonInspect())
    assert not value.initialized and not any(value.counts.values())
    assert value.applied_migrations == [] and value.history == {'events_verified':0,'head':None}
    empty = owner.inbox(CanonInbox())
    assert empty.receiver is None and empty.packet_count == 0 and not empty.truncated
    assert 'owner_migrations_and_history_files' not in value.verified_scope
    with pytest.raises(LaneError, match='Select an existing receiver') as caught:
        owner.inbox(CanonInbox(receiver_id=str(uuid4())))
    assert caught.value.code == 'CANON_PARTICIPANT_NOT_FOUND'
    assert project.pv_head() == before[0] and project.lane_catalog() == before[1]


def test_read_budgets_do_not_report_partial_integrity_success(pair):
    invoke, _, receiver, _, target, (_, project, _, _) = pair
    send(pair)
    before = project.pv_head()
    assert invoke(receiver,'task_evidence_inspect',{'record_limit':1}).error.code == 'CANON_INSPECTION_BUDGET'
    assert invoke(receiver,'task_evidence_inbox',{'receiver_id':target,'history_limit':1}).error.code == 'CANON_HISTORY_BUDGET'
    assert project.pv_head() == before


def test_project_inboxes_preserve_multiple_receiver_routes_without_identity_inference(pair):
    invoke, sender, receiver, source, target, _ = pair
    first, _ = send(pair)
    second = invoke(receiver, 'task_evidence_send', CanonSend(sender_id=target, receiver_id=source,
        kind='evidence', payload={'summary':'Another receiver inbox'}))
    assert second.status == 'ok'
    page = invoke(sender, 'task_evidence_inbox', {}).result
    assert page['receiver'] is None and page['packet_count'] == 2
    assert {item['exchange_id'] for item in page['packets']} == {first.result['exchange_id'],second.result['exchange_id']}
    assert {item['receiver_id'] for item in page['packets']} == {source,target}
    assert page['native_task_attestation'] == 'not_provided'


def test_inbox_byte_budget_can_be_resolved_by_bounded_event_pagination(pair):
    invoke, _, receiver, _, target, (engine, project, _, _) = pair
    sent, _ = send(pair)
    owner = CanonStore(project)
    with engine.project_work.mutation(project) as lease, lease.coordinated_transaction(['canon','receipts']):
        for version in range(1,21):
            owner.decide(CanonDecide(exchange_id=sent.result['exchange_id'],envelope_digest=sent.result['envelope_digest'],
                expected_version=version,decision='clarify',reason='A'*1000),lease,actor_id=receiver.client_id)
    before = project.pv_head()
    oversized = invoke(receiver,'task_evidence_inbox',{'receiver_id':target,'max_bytes':16384,'events_per_packet':50})
    assert oversized.error.code == 'CANON_INBOX_ITEM_BUDGET'
    bounded = invoke(receiver,'task_evidence_inbox',{'receiver_id':target,'max_bytes':16384,'events_per_packet':2})
    assert bounded.status == 'ok', bounded
    assert bounded.result['packets'][0]['events_truncated']
    assert len(json.dumps(bounded.result).encode()) <= 16384
    assert project.pv_head() == before


@pytest.mark.parametrize('action', ['task_evidence_inbox','task_evidence_inspect'])
def test_damaged_event_json_cannot_disappear_from_inspection(pair, action):
    invoke, _, receiver, _, target, (engine, project, _, _) = pair
    sent, request = send(pair)
    with engine.project_work.mutation(project) as lease, lease.transaction('canon') as connection:
        connection.execute('UPDATE canon_events SET result_json=? WHERE request_id=?', ('{}',request.request_id))
    arguments = {'receiver_id':target} if action == 'task_evidence_inbox' else {}
    assert invoke(receiver,action,arguments).error.code == 'CANON_HISTORY_INTEGRITY'
    assert sent.result['state'] == 'received'


@pytest.mark.parametrize(('statement','code'), [
    ("UPDATE canon_participants SET digest='"+'0'*64+"'", 'CANON_PARTICIPANT_INTEGRITY'),
    ("UPDATE canon_exchanges SET state='admitted'", 'CANON_STATE_INTEGRITY'),
])
def test_inspector_validates_objects_beyond_sqlite_integrity(pair, statement, code):
    invoke, _, receiver, _, _, (engine, project, _, _) = pair
    send(pair)
    with engine.project_work.mutation(project) as lease, lease.transaction('canon') as connection:
        connection.execute(statement)
    assert invoke(receiver,'task_evidence_inspect',{}).error.code == code


def test_current_contract_pointer_is_checked_against_its_receiver_and_key(pair):
    invoke, _, receiver, source, target, (engine, project, _, _) = pair
    for key in ('first','second'):
        assert invoke(receiver, 'task_evidence_expect', CanonExpected(receiver_id=target, contract_key=key,
            sender_ids=[source], kinds=['evidence'])).status == 'ok'
    with engine.project_work.mutation(project) as lease, lease.transaction('canon') as connection:
        connection.execute("UPDATE canon_contract_current SET contract_digest=(SELECT contract_digest FROM canon_contract_current WHERE contract_key='second') WHERE contract_key='first'")
    assert invoke(receiver,'task_evidence_inspect',{}).error.code == 'CANON_CONTRACT_INTEGRITY'


def test_inspection_actions_through_read_only_mcp_and_real_backend(pair):
    import asyncio
    import sys
    from datetime import timedelta
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _, _, _, _, target, (engine, project, _, _) = pair
    sent, _ = send(pair)
    decide(pair, sent)
    before = project.pv_head()
    plugin = Path(__file__).resolve().parents[1]/'plugins/evidence-lane-plugin'

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m','evidence_lane_plugin.mcp_adapter','--runtime-root',str(engine.root),
                  '--project-id',project.project_id,'--permission','read'],env={'PYTHONPATH':str(plugin/'src')})
        async with (stdio_client(parameters) as (read,write),
                    ClientSession(read,write,read_timeout_seconds=timedelta(seconds=30)) as session):
            await session.initialize()
            for action,arguments in [('task_evidence_inbox',{'receiver_id':target}),('task_evidence_inspect',{})]:
                response = (await session.call_tool(action,{'project_id':project.project_id,'arguments':arguments})).structuredContent
                assert response and response['status'] == 'ok', response
                assert response['result']['project_mutated'] is False
                assert response['tool_execution']['env_uop']['action_name'] == action
                assert response['tool_execution']['native_host_tool_attested'] is False
    with LocalEndpoint(engine,studio_enabled=False):
        asyncio.run(exercise())
    assert project.pv_head() == before
