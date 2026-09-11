"""The original standalone supersession action and correction paths share history."""
# ruff: noqa: F811 -- pytest resolves explicitly imported fixtures by name.
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import CanonDecide, CanonStore, CanonSupersede
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import content_digest

from tests.test_canon_classification_v4 import expected, message
from tests.test_canon_v4 import decide, pair, send  # noqa: F401
from tests.test_session_v4 import selected as system  # noqa: F401


def admitted_pair(pair):
    first, _ = send(pair)
    decided, _ = decide(pair, first)
    second, _ = send(pair, payload={'summary': 'Newer scoped input'})
    decide(pair, second)
    return first, second, CanonSupersede(exchange_id=first.result['exchange_id'],
        envelope_digest=first.result['envelope_digest'], expected_version=decided.result['version'],
        successor_id=second.result['exchange_id'], successor_envelope_digest=second.result['envelope_digest'])


def states(pair):
    return {row['exchange_id']:row for row in CanonStore(pair[5][1]).read().exchanges}


def test_standalone_supersession_preserves_envelopes_and_records_exact_successor(pair):
    invoke, sender, receiver, _, _, (_, project, _, _) = pair
    first, second, request = admitted_pair(pair)
    before = {row['lane_id']:row for row in project.lane_catalog() if row['lane_id'] not in {'canon','receipts'}}
    before_history = CanonStore(project).verify_history()['events_verified']
    assert invoke(sender, 'canon_supersede', request).error.code == 'CANON_OWNER_MISMATCH'
    response = invoke(receiver, 'canon_supersede', request)
    assert response.status == 'ok', response
    assert response.result['version'] == 3 and response.result['decision_basis'] == 'receiver_explicit'
    current = states(pair)
    old = current[first.result['exchange_id']]
    assert old['state'] == 'superseded' and old['envelope_digest'] == first.result['envelope_digest']
    assert old['supersession']['successor_id'] == second.result['exchange_id']
    assert old['supersession_evidence'] == 'recorded_event'
    assert current[second.result['exchange_id']]['state'] == 'admitted'
    assert CanonStore(project).verify_history()['events_verified'] == before_history + 1
    assert {row['lane_id']:row for row in project.lane_catalog() if row['lane_id'] not in {'canon','receipts'}} == before
    with project.lane('canon').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='canon_supersessions'").fetchone()
        assert connection.execute('SELECT count(*) FROM canon_exchanges').fetchone()[0] == 2


def test_exact_supersession_replay_cannot_repoint_the_original(pair):
    invoke, _, receiver, _, _, (_, project, _, _) = pair
    _, _, request = admitted_pair(pair)
    first = invoke(receiver, 'canon_supersede', request)
    assert first.status == 'ok', first
    head = CanonStore(project).verify_history()
    replay = invoke(receiver, 'canon_supersede', request)
    assert replay.result == {**first.result, 'duplicate':True}
    assert CanonStore(project).verify_history() == head
    third, _ = send(pair, payload={'summary':'Another successor'})
    decide(pair, third)
    conflict = request.model_copy(update={'request_id':str(uuid4()), 'expected_version':3,
        'successor_id':third.result['exchange_id'], 'successor_envelope_digest':third.result['envelope_digest']})
    assert invoke(receiver, 'canon_supersede', conflict).error.code == 'CANON_SUPERSESSION_REPLAY_CONFLICT'
    assert invoke(receiver, 'canon_supersede', conflict.model_copy(update={'request_id':request.request_id})).error.code == 'CANON_REQUEST_CONFLICT'


@pytest.mark.parametrize('changes', [
    {'expected_version':1}, {'envelope_digest':'0'*64}, {'successor_envelope_digest':'0'*64},
])
def test_stale_versions_and_envelopes_cannot_supersede(pair, changes):
    invoke, _, receiver, _, _, _ = pair
    first, _, request = admitted_pair(pair)
    response = invoke(receiver, 'canon_supersede', request.model_copy(update=changes))
    assert response.error.code == 'CANON_SUPERSESSION_VERSION_CONFLICT'
    assert states(pair)[first.result['exchange_id']]['state'] == 'admitted'


def test_reverse_order_and_different_route_fail(pair):
    invoke, sender, receiver, _, _, _ = pair
    first, second, request = admitted_pair(pair)
    backwards = CanonSupersede(exchange_id=second.result['exchange_id'], envelope_digest=second.result['envelope_digest'],
        expected_version=2, successor_id=first.result['exchange_id'], successor_envelope_digest=first.result['envelope_digest'])
    assert invoke(receiver, 'canon_supersede', backwards).error.code == 'CANON_SUPERSESSION_LINEAGE_INVALID'
    third = invoke(receiver, 'canon_send', {'sender_id':pair[4], 'receiver_id':pair[3], 'kind':'requirements',
        'payload':{'summary':'Opposite route'}})
    assert third.status == 'ok', third
    assert invoke(sender, 'canon_decide', CanonDecide(exchange_id=third.result['exchange_id'],
        envelope_digest=third.result['envelope_digest'], expected_version=1, decision='admit', reason='Review', incompatible_input_decision='ACCEPT')).status == 'ok'
    wrong = request.model_copy(update={'successor_id':third.result['exchange_id'],
        'successor_envelope_digest':third.result['envelope_digest']})
    assert invoke(receiver, 'canon_supersede', wrong).error.code == 'CANON_SUPERSESSION_LINEAGE_INVALID'


def test_pending_successor_and_undecided_original_do_not_replace_input(pair):
    invoke, _, receiver, _, _, _ = pair
    first, _ = send(pair)
    second, _ = send(pair)
    request = CanonSupersede(exchange_id=first.result['exchange_id'], envelope_digest=first.result['envelope_digest'],
        expected_version=1, successor_id=second.result['exchange_id'], successor_envelope_digest=second.result['envelope_digest'])
    assert invoke(receiver, 'canon_supersede', request).error.code == 'CANON_SUPERSESSION_STATE_INVALID'
    decide(pair, first)
    assert invoke(receiver, 'canon_supersede', request.model_copy(update={'expected_version':2})).error.code == 'CANON_SUPERSESSION_SUCCESSOR_NOT_ADMITTED'
    assert all(row['state'] in {'received','admitted'} for row in states(pair).values())


@pytest.mark.parametrize('automatic', [True, False])
def test_correction_admission_records_the_same_successor_proof(pair, automatic):
    invoke, sender, _, _, _, _ = pair
    original, _ = send(pair)
    decide(pair, original)
    _, digest = expected(pair, auto_admit=automatic)
    correction = invoke(sender, 'canon_send', message(pair, kind='correction', supersedes=original.result['exchange_id'],
        expected_contract=digest))
    assert correction.status == 'ok', correction
    if not automatic:
        assert states(pair)[original.result['exchange_id']]['state'] == 'admitted'
        decide(pair, correction)
    link = states(pair)[original.result['exchange_id']]['supersession']
    assert link['successor_id'] == correction.result['exchange_id']
    assert link['decision_basis'] == ('receiver_expected_contract' if automatic else 'receiver_correction_admission')


def test_failed_supersession_receipt_rolls_back_state_event_and_successor_admission(pair, monkeypatch):
    invoke, _, receiver, _, _, (_, project, _, _) = pair
    original, _ = send(pair)
    decide(pair, original)
    correction, _ = send(pair, kind='correction', supersedes=original.result['exchange_id'])
    before = CanonStore(project).verify_history()
    owner = type(project.lane('canon'))
    append = owner.append_receipt
    def fail(self, kind, *args, **kwargs):
        identity = append(self, kind, *args, **kwargs)
        if kind == 'canon_superseded':
            raise LaneError('INJECTED_CANON_SUPERSESSION_FAILURE', 'Failure after provisional supersession.')
        return identity
    monkeypatch.setattr(owner, 'append_receipt', fail)
    response = invoke(receiver, 'canon_decide', CanonDecide(exchange_id=correction.result['exchange_id'],
        envelope_digest=correction.result['envelope_digest'], expected_version=1, decision='admit', reason='Review', incompatible_input_decision='ACCEPT'))
    assert response.error.code == 'INJECTED_CANON_SUPERSESSION_FAILURE'
    assert CanonStore(project).verify_history() == before
    current = states(pair)
    assert current[original.result['exchange_id']]['state'] == 'admitted'
    assert current[correction.result['exchange_id']]['state'] == 'received'


def test_supersession_event_corruption_is_not_a_valid_replay_or_read(pair):
    invoke, _, receiver, _, _, (engine, project, _, _) = pair
    _, _, request = admitted_pair(pair)
    assert invoke(receiver, 'canon_supersede', request).status == 'ok'
    with engine.project_work.mutation(project) as lease, lease.transaction('canon') as connection:
        connection.execute('UPDATE canon_events SET result_json=? WHERE request_id=?', ('{}',request.request_id))
    assert invoke(receiver, 'canon_supersede', request).error.code == 'CANON_HISTORY_INTEGRITY'
    assert invoke(receiver, 'canon_read', {}).error.code == 'CANON_HISTORY_INTEGRITY'
    # An intact digest with a changed successor is also insufficient.
    with engine.project_work.mutation(project) as lease, lease.transaction('canon') as connection:
        event = dict(connection.execute('SELECT * FROM canon_events WHERE request_id=?',(request.request_id,)).fetchone())
        event.pop('digest')
        result = request.model_dump(exclude={'request_id','expected_version'}) | {
            'successor_id':request.exchange_id, 'receiver_id':pair[4], 'version':3,
            'decision_basis':'receiver_explicit', 'state':'superseded', 'duplicate':False,
            'plan_mutated':False, 'source_write_granted':False}
        event['result_json'] = json.dumps(result, sort_keys=True, separators=(',',':'))
        connection.execute('UPDATE canon_events SET result_json=?,digest=? WHERE request_id=?',
            (event['result_json'],content_digest(event),request.request_id))
    assert invoke(receiver, 'canon_read', {}).error.code == 'CANON_SUPERSESSION_INTEGRITY'


def test_consequence_graph_distinguishes_pending_correction_from_actual_supersession(pair):
    from evidence_lane_plugin.canon_consequence_graph import canon_view
    from evidence_lane_plugin.lane_contract import ViewScope

    _, _, _, _, _, (_, project, _, _) = pair
    original, _ = send(pair)
    decide(pair, original)
    correction, _ = send(pair, kind='correction', supersedes=original.result['exchange_id'])
    before = project.pv_head()
    pending = canon_view(project, ViewScope())
    assert project.pv_head() == before
    assert len([edge for edge in pending['edges'] if edge['kind'] == 'CORRECTS']) == 1
    assert not any(edge['kind'] == 'SUPERSEDES' for edge in pending['edges'])
    decide(pair, correction)
    before = project.pv_head()
    admitted = canon_view(project, ViewScope())
    assert project.pv_head() == before
    nodes = {node['id']:node['key'] for node in admitted['nodes']}
    link = next(edge for edge in admitted['edges'] if edge['kind'] == 'SUPERSEDES')
    assert nodes[link['source']] == correction.result['exchange_id']
    assert nodes[link['target']] == original.result['exchange_id']
    assert link['provenance'] == 'receiver_correction_admission'
    assert link['evidence']['envelope_digest'] == correction.result['envelope_digest']


def test_supersession_through_real_mcp_and_local_backend(system):
    import asyncio
    import sys
    from datetime import timedelta
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine, project, _, _ = system
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                  '--project-id', project.project_id, '--permission', 'write'],
            env={'PYTHONPATH':str(plugin/'src')})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
            await session.initialize()
            async def invoke(action, arguments):
                result = await session.call_tool(action, {'project_id':project.project_id, 'arguments':arguments})
                response = result.structuredContent
                assert response and response['status'] == 'ok', result
                return response
            sender = (await invoke('canon_join', {'label':'Source participant'}))['result']['participant_id']
            receiver = (await invoke('canon_join', {'label':'Receiver participant'}))['result']['participant_id']
            items = []
            for summary in ('Original requirement', 'Newer requirement'):
                sent = (await invoke('canon_send', {'sender_id':sender, 'receiver_id':receiver,
                    'kind':'requirements', 'payload':{'summary':summary}}))['result']
                await invoke('canon_decide', {'exchange_id':sent['exchange_id'], 'envelope_digest':sent['envelope_digest'],
                    'expected_version':1, 'decision':'admit', 'reason':'Receiver explicitly accepted this input.', 'incompatible_input_decision':'ACCEPT'})
                items.append(sent)
            response = await invoke('canon_supersede', {'exchange_id':items[0]['exchange_id'],
                'envelope_digest':items[0]['envelope_digest'], 'expected_version':2,
                'successor_id':items[1]['exchange_id'], 'successor_envelope_digest':items[1]['envelope_digest']})
            assert response['result']['state'] == 'superseded'
            assert response['tool_execution']['env_uop']['action_name'] == 'canon_supersede'
            assert response['tool_execution']['native_host_tool_attested'] is False

    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(exercise())
