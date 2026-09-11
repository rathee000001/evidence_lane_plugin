"""Sealed Canon context at the existing project continuation boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import CanonExpected, CanonJoin, CanonSend, CanonStore
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore, content_digest
from evidence_lane_plugin.storage import json_text
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.task_binding_registry import (
    ContinuationAccept,
    ContinuationCancel,
    TaskContinuity,
)

from tests.test_canon_v4 import decide, pair, send
from tests.test_delta_entry import plan, system
from tests.test_native_workflow_bindings import native
from tests.test_task_continuity import accept, offer

__all__ = ['pair', 'system']


def files(project):
    return {str(path.relative_to(project.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.root.rglob('*') if path.is_file()}


def continuation_authority_files(project):
    # A rejected write still records writer acquisition in Receipts and Root PV.
    # These owner bytes must stay unchanged, including all published lane files.
    return {str(path.relative_to(project.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for lane in ('canon', 'chat_lineage', 'plan')
        for path in project.lane(lane).folder.rglob('*') if path.is_file()}


def test_offer_seals_all_source_participants_pending_locators_without_payload_or_decisions(pair):
    invoke, sender, receiver, source, target, system = pair
    plan(system)
    extra = invoke(sender, 'task_evidence_participant_register', CanonJoin(label='Second transferred role')).result['participant_id']
    pending, _ = send(pair, payload={'summary':'Private-to-envelope payload is not a checkpoint field'})
    incoming = invoke(receiver, 'task_evidence_send', CanonSend(sender_id=target, receiver_id=extra,
        kind='evidence', payload={'summary':'Second role incoming'}))
    assert incoming.status == 'ok'
    admitted, _ = send(pair)
    decide(pair, admitted)
    before = CanonStore(system[1]).read().model_dump(), PlanStore(system[1]).snapshot().model_dump()
    offered, request = offer(pair)
    assert invoke(sender, 'continuation_offer', request).result == offered.result
    bound = TaskContinuity(system[1]).read().offers[0]['binding']
    sealed = bound['canon_checkpoint']
    assert sealed['participant_ids'] == sorted([source, extra])
    assert {item['exchange_id'] for item in sealed['pending']} == {
        pending.result['exchange_id'], incoming.result['exchange_id']}
    assert all(item['state'] == 'received' and item['version'] == 1 for item in sealed['pending'])
    assert offered.result['canon_checkpoint_digest'] == content_digest(sealed) == bound['canon_checkpoint_digest']
    assert not sealed['decision_tokens_carried'] and not sealed['canon_state_mutated']
    assert 'Private-to-envelope' not in json.dumps(sealed) and 'summary' not in json.dumps(sealed)
    assert (CanonStore(system[1]).read().model_dump(), PlanStore(system[1]).snapshot().model_dump()) == before


@pytest.mark.parametrize('change', ['incoming', 'decision', 'contract'])
def test_changed_canon_requires_fresh_offer_before_any_owner_change(pair, change):
    invoke, sender, receiver, source, target, system = pair
    plan(system)
    pending, _ = send(pair)
    offered, _ = offer(pair)
    if change == 'incoming':
        result = invoke(receiver, 'task_evidence_send', CanonSend(sender_id=target, receiver_id=source,
            kind='evidence', payload={'summary':'New input'}))
        assert result.status == 'ok'
    elif change == 'decision':
        decide(pair, pending)
    else:
        result = invoke(receiver, 'task_evidence_expect', CanonExpected(receiver_id=target,
            contract_key='new_contract', sender_ids=[source], kinds=['requirements']))
        assert result.status == 'ok'
    before = continuation_authority_files(system[1])
    refused, _ = accept(pair, offered)
    assert refused.error.code == 'CONTINUATION_CANON_CHANGED'
    assert continuation_authority_files(system[1]) == before
    assert TaskContinuity(system[1]).read().offers[0]['state'] == 'offered'
    cancelled = invoke(sender, 'continuation_cancel', ContinuationCancel(
        continuation_id=offered.result['continuation_id'], continuation_digest=offered.result['continuation_digest'],
        reason='Review current Canon input'))
    assert cancelled.status == 'ok'
    fresh, _ = offer(pair)
    assert accept(pair, fresh)[0].status == 'ok'


def test_restored_context_distinguishes_sealed_pending_from_current_receiver_decision(pair):
    invoke, _, receiver, _, _, system = pair
    plan(system)
    sent, _ = send(pair)
    offered, _ = offer(pair)
    before_canon = CanonStore(system[1]).read().exchanges
    result, request = accept(pair, offered)
    assert result.status == 'ok', result.error
    assert result.result['canon_checkpoint_digest'] == offered.result['canon_checkpoint_digest']
    assert result.result['canon_decisions_replayed'] is False
    assert invoke(receiver, 'continuation_accept', request).result == result.result
    assert CanonStore(system[1]).read().exchanges == before_canon
    decide(pair, sent)
    before = files(system[1])
    context = invoke(receiver, 'continuation_context', {
        'continuation_id':request.continuation_id, 'continuation_digest':request.continuation_digest}).result
    sealed = context['canon_continuity']
    assert sealed['checkpoint_present'] and sealed['canon_changed_since_offer']
    assert sealed['pending_at_offer'][0]['state'] == 'received'
    assert context['canon']['exchanges'][0]['state'] == 'admitted'
    assert sealed['restore_effect'] == 'context_only' and not sealed['canon_decisions_replayed']
    assert not context['host_session_attached'] and context['native_task_attestation'] == 'not_provided'
    assert files(system[1]) == before


def test_pending_budget_and_corrupt_envelope_fail_without_publishing_an_offer(pair):
    _, _, _, source, _, system = pair
    plan(system)
    sent, _ = send(pair)
    send(pair)
    with pytest.raises(LaneError) as error:
        CanonStore(system[1]).continuation_checkpoint([source], limit=1)
    assert error.value.code == 'CANON_CONTINUITY_BUDGET'
    # An isolated corrupt source fixture must not become a valid checkpoint.
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('canon') as connection:
        connection.execute('UPDATE canon_exchanges SET envelope_digest=? WHERE exchange_id=?',
            ('0' * 64, sent.result['exchange_id']))
    before = files(system[1])
    with pytest.raises(LaneError) as error:
        CanonStore(system[1]).continuation_checkpoint([source])
    assert error.value.code == 'CANON_ENVELOPE_INTEGRITY'
    assert files(system[1]) == before


def test_historical_offer_is_readable_but_must_be_replaced_before_accept(pair):
    invoke, _, receiver, _, _, system = pair
    plan(system)
    offered, _ = offer(pair)
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('chat_lineage') as connection:
        body = json.loads(connection.execute('SELECT body_json FROM continuation_offers').fetchone()[0])
        body.pop('canon_checkpoint')
        body.pop('canon_checkpoint_digest')
        historical = content_digest(body)
        connection.execute('UPDATE continuation_offers SET body_json=?,continuation_digest=?', (json_text(body), historical))
    assert TaskContinuity(system[1]).read().offers[0]['state'] == 'offered'
    before = continuation_authority_files(system[1])
    refused = invoke(receiver, 'continuation_accept', ContinuationAccept(
        continuation_id=offered.result['continuation_id'], continuation_digest=historical))
    assert refused.error.code == 'CONTINUATION_CANON_CHECKPOINT_REQUIRED'
    assert continuation_authority_files(system[1]) == before


def test_checkpoint_digest_mismatch_rejected_even_if_outer_offer_rehashed(pair):
    invoke, _, receiver, _, _, system = pair
    plan(system)
    offered, _ = offer(pair)
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('chat_lineage') as connection:
        body = json.loads(connection.execute('SELECT body_json FROM continuation_offers').fetchone()[0])
        body['canon_checkpoint']['event_sequence'] += 1
        changed = content_digest(body)
        connection.execute('UPDATE continuation_offers SET body_json=?,continuation_digest=?', (json_text(body), changed))
    before = continuation_authority_files(system[1])
    refused = invoke(receiver, 'continuation_accept', ContinuationAccept(
        continuation_id=offered.result['continuation_id'], continuation_digest=changed))
    assert refused.error.code == 'CONTINUATION_CANON_INTEGRITY'
    assert continuation_authority_files(system[1]) == before


def test_mcp_continuation_checkpoint_and_read_only_studio(system):
    engine, project, _, _ = system
    task = plan(system)
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            async with (
                native(engine.root, project.project_id, permissions=('read', 'write')) as source_session,
                native(engine.root, project.project_id, permissions=('read', 'write')) as destination_session,
            ):
                    async def call(session, action, arguments):
                        response = await session.call_tool(action, {'project_id':project.project_id, 'arguments':arguments})
                        assert not response.isError and response.structuredContent['status'] == 'ok', response
                        return response.structuredContent['result']
                    source = await call(source_session, 'task_evidence_participant_register', {'request_id':str(uuid4()), 'label':'Protocol source'})
                    destination = await call(destination_session, 'task_evidence_participant_register', {'request_id':str(uuid4()), 'label':'Protocol destination'})
                    await call(source_session, 'task_evidence_send', {'sender_id':source['participant_id'],
                        'receiver_id':destination['participant_id'], 'kind':'evidence', 'payload':{'summary':'Visible input'}})
                    offered = await call(source_session, 'continuation_offer', {'participant_id':source['participant_id'],
                        'destination_participant_id':destination['participant_id'], 'task_id':'first',
                        'plan_revision':1, 'contract_digest':task.contract_digest})
                    args = {key:offered[key] for key in ['continuation_id','continuation_digest']}
                    accepted = await call(destination_session, 'continuation_accept', args)
                    assert accepted['canon_checkpoint_digest'] == offered['canon_checkpoint_digest']
                    context = await call(destination_session, 'continuation_context', args)
                    assert context['canon_continuity']['pending_count_at_offer'] == 1
                    denied = await source_session.call_tool('task_evidence_participant_register', {'project_id':project.project_id,
                        'arguments':{'label':'Closed source cannot write'}})
                    assert denied.isError and denied.structuredContent['error']['code'] == 'SOURCE_CLIENT_CLOSEOUT_ONLY'
                    return args
        args = asyncio.run(exercise())
    gateway = StudioGateway(engine)
    _, studio = gateway.exchange(gateway.issue_ticket())
    before = files(project)
    page = gateway.command('read', {'project_id':project.project_id, 'action':'continuation_read'}, studio)
    assert page['offers'][0]['binding']['canon_checkpoint']['pending'][0]['state'] == 'received'
    with pytest.raises(LaneError) as error:
        gateway.command('read', {'project_id':project.project_id, 'action':'continuation_accept', 'arguments':args}, studio)
    assert error.value.code == 'NOT_A_STUDIO_QUERY'
    assert files(project) == before
