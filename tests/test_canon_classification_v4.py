"""Original Canon preview behavior through the full, locked v4 engine."""
# ruff: noqa: F811 - imported pytest fixtures are resolved by parameter name
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import CanonExpected, CanonSend, CanonStore
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.mcp_adapter import tool_from_action
from evidence_lane_plugin.plan_runtime import content_digest

from tests.test_canon_v4 import (  # noqa: F401 - run the retained behaviors with the actual registry
    decide,
    pair,
    send,
    test_cross_project_claims_and_invalid_private_payload_do_not_admit,
    test_envelope_integrity_and_byte_budget,
    test_evidence_checks_and_failed_write_rollback_are_atomic,
    test_memory_references_keep_canon_state_and_queries_are_read_only,
    test_pending_correction_preserves_original_until_receiver_admission,
    test_receiver_expected_contract_is_typed_versioned_and_exact,
    test_result_backfire_return_route_and_clarification_keep_ownership,
    test_return_contract_is_pinned_to_source_expectation_and_chain_is_bounded,
    test_state_version_corruption_is_rejected_before_admission,
)
from tests.test_session_v4 import (
    selected as system,  # noqa: F401 - exact default engine fixture
)


def message(pair, **changes):
    values = {'sender_id': pair[3], 'receiver_id': pair[4], 'kind': 'evidence',
              'payload': {'summary': 'Two bounded observations', 'fields': {'count': 2}}}
    values.update(changes)
    return CanonSend.model_validate(values)


def expected(pair, *, auto_admit=True):
    invoke, _, receiver, source, target, _ = pair
    request = CanonExpected(receiver_id=target, contract_key='preview', sender_ids=[source],
        kinds=['evidence', 'correction'], fields={'count': 'integer'}, auto_admit=auto_admit)
    response = invoke(receiver, 'task_evidence_expect', request)
    assert response.status == 'ok', response.error
    return request, response.result['contract_digest']


@pytest.mark.parametrize('automatic', [True, False])
def test_classification_is_read_only_and_predicts_receiver_admission(pair, automatic):
    invoke, sender, receiver, _, _, (_, project, _, _) = pair
    _, digest = expected(pair, auto_admit=automatic)
    request = message(pair, expected_contract=digest)
    before = project.pv_head()
    history = CanonStore(project).verify_history()
    response = invoke(receiver, 'task_evidence_classify', request)
    assert response.status == 'ok', response.error
    result = response.result
    assert result['classification'] == 'expected' and result['reasons'] == []
    assert result['automatic_admission_permitted'] is automatic
    assert result['receiver_decision_required'] is not automatic
    assert result['message_digest'] == content_digest(request.model_dump(exclude={'request_id'}))
    assert not result['admission_performed'] and not result['project_mutated']
    assert result['native_task_attestation'] == 'not_provided'
    assert project.pv_head() == before and CanonStore(project).verify_history() == history
    assert CanonStore(project).read().exchanges == []
    sent = invoke(sender, 'task_evidence_send', request)
    assert sent.status == 'ok', sent.error
    assert sent.result['state'] == ('admitted' if automatic else 'received')


def test_undefined_missing_and_replaced_contracts_stay_unadmitted(pair):
    invoke, _, receiver, _, _, (_, project, _, _) = pair
    request, digest = expected(pair)
    cases = [({}, 'CANON_EXPECTED_CONTRACT_UNDEFINED'),
             ({'expected_contract': '0' * 64}, 'CANON_CONTRACT_NOT_FOUND'),
             ({'expected_contract': digest, 'payload': {'summary': 'Bad scalar', 'fields': {'count': True}}},
              'CANON_PAYLOAD_TYPE_MISMATCH')]
    for fields, reason in cases:
        before = project.pv_head()
        result = invoke(receiver, 'task_evidence_classify', message(pair, **fields))
        assert result.status == 'ok', result.error
        assert result.result['classification'] == 'undefined_or_incompatible'
        assert result.result['reasons'] == [reason]
        assert project.pv_head() == before
    revision = request.model_copy(update={'request_id': str(uuid4()), 'expected_version': 1})
    assert invoke(receiver, 'task_evidence_expect', revision).status == 'ok'
    stale = invoke(receiver, 'task_evidence_classify', message(pair, expected_contract=digest))
    assert stale.result['reasons'] == ['CANON_CONTRACT_MISMATCH']
    assert not stale.result['automatic_admission_permitted']


def test_sender_and_contract_are_rechecked_after_preview(pair):
    invoke, sender, receiver, _, _, _ = pair
    contract, digest = expected(pair)
    request = message(pair, expected_contract=digest)
    assert invoke(receiver, 'task_evidence_classify', request).result['classification'] == 'expected'
    # Previewing another participant's message cannot grant its source identity.
    assert invoke(receiver, 'task_evidence_send', request).error.code == 'CANON_OWNER_MISMATCH'
    changed = contract.model_copy(update={'request_id': str(uuid4()), 'expected_version': 1, 'active': False})
    assert invoke(receiver, 'task_evidence_expect', changed).status == 'ok'
    pending = invoke(sender, 'task_evidence_send', request)
    assert pending.result['state'] == 'received' and pending.result['compatibility_reasons'] == ['CANON_CONTRACT_MISMATCH']


@pytest.mark.parametrize('change,reason', [
    ({'expires_at': (datetime.now(UTC) - timedelta(days=1)).isoformat()}, 'CANON_EXCHANGE_EXPIRED'),
    ({'sender_id': str(uuid4())}, 'CANON_PARTICIPANT_NOT_FOUND'),
    ({'payload': {'summary': 'Missing evidence', 'references': [
        {'kind': 'source_object', 'key': '0' * 64, 'digest': '0' * 64, 'profile': 'code', 'lane_id': 'local_code'}]}},
     'MEMORY_SOURCE_MISMATCH'),
    ({'kind': 'result', 'reply_to': str(uuid4())}, 'CANON_EXCHANGE_NOT_FOUND'),
])
def test_preview_reports_exact_incompatibility_without_writes(pair, change, reason):
    invoke, _, receiver, _, _, (_, project, _, _) = pair
    before = project.pv_head()
    result = invoke(receiver, 'task_evidence_classify', message(pair, **change))
    assert result.status == 'ok', result.error
    assert result.result['reasons'] == [reason]
    assert result.result['classification'] == 'undefined_or_incompatible'
    assert project.pv_head() == before


def test_corrupt_contract_is_failure_not_receiver_overridable_mismatch(pair):
    invoke, _, receiver, _, _, (engine, project, _, _) = pair
    _, digest = expected(pair)
    with engine.project_work.mutation(project) as lease, lease.transaction('canon') as connection:
        connection.execute('UPDATE canon_contracts SET version=3 WHERE contract_digest=?', (digest,))
    response = invoke(receiver, 'task_evidence_classify', message(pair, expected_contract=digest))
    assert response.status == 'error' and response.error.code == 'CANON_CONTRACT_INTEGRITY'


def test_preview_uses_exact_return_and_correction_paths(pair):
    invoke, _, receiver, source, target, (_, project, _, _) = pair
    original, _ = send(pair)
    returned = message(pair, sender_id=target, receiver_id=source, kind='result',
                       reply_to=original.result['exchange_id'])
    assert invoke(receiver, 'task_evidence_classify', returned).result['reasons'] == ['CANON_RETURN_REQUIRES_INPUT']
    decide(pair, original)
    _, digest = expected(pair)
    correction = message(pair, kind='correction', supersedes=original.result['exchange_id'], expected_contract=digest)
    before = CanonStore(project).read().model_dump()
    response = invoke(receiver, 'task_evidence_classify', correction)
    assert response.result['classification'] == 'expected'
    assert CanonStore(project).read().model_dump() == before


def test_read_only_client_and_mcp_contract_expose_preview_without_mutation(pair):
    invoke, _, _, _, _, (engine, project, _, _) = pair
    _, digest = expected(pair)
    _, observer = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
        project_id=project.project_id, permissions=['read'])]))
    response = invoke(observer, 'task_evidence_classify', message(pair, expected_contract=digest))
    assert response.status == 'ok' and response.result['classification'] == 'expected'
    assert invoke(observer, 'task_evidence_send', message(pair)).error.code == 'PROJECT_NOT_SELECTED'
    spec = engine.registry.get('task_evidence_classify')
    tool = tool_from_action(spec.schema())
    assert tool.annotations.readOnlyHint is True and tool.annotations.destructiveHint is False
    assert spec.queryable_in_delta and not spec.mutates and spec.permission == 'read'


def test_empty_canon_lane_is_not_initialized_by_classification(system):
    from tests.test_session_v4 import call
    engine, project, _, client = system
    before = project.pv_head()
    request = CanonSend(sender_id=str(uuid4()), receiver_id=str(uuid4()), kind='evidence',
                        payload={'summary': 'Unregistered participants'})
    response = call(engine, project, client, 'task_evidence_classify', request.model_dump())
    assert response.result['reasons'] == ['CANON_PARTICIPANT_NOT_FOUND']
    assert project.pv_head() == before
    with project.lane('canon').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='canon_participants'").fetchone()


def test_first_canon_mutation_rolls_back_schema_and_participant_together(system, monkeypatch):
    from tests.test_session_v4 import call
    engine, project, _, client = system
    before = {item['lane_id']: item for item in project.lane_catalog() if item['lane_id'] != 'receipts'}
    original = CanonStore._event

    def failed_event(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Injected first Canon event failure')

    with monkeypatch.context() as patch:
        patch.setattr(CanonStore, '_event', staticmethod(failed_event))
        result = call(engine, project, client, 'task_evidence_participant_register', {'label': 'Will roll back'})
    assert result.error.code == 'TOOL_ADAPTER_FAILED'
    with project.lane('canon').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='canon_participants'").fetchone()
    assert {item['lane_id']: item for item in project.lane_catalog() if item['lane_id'] != 'receipts'} == before
    assert call(engine, project, client, 'task_evidence_participant_register', {'label': 'Verified participant'}).status == 'ok'


def test_canon_preview_through_real_mcp_stdio_and_local_backend(pair):
    import asyncio
    import sys
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _, _, _, _, _, (engine, project, _, _) = pair
    _, digest = expected(pair)
    request = message(pair, expected_contract=digest)
    before = project.pv_head()
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                  '--project-id', project.project_id, '--permission', 'read'],
            env={'PYTHONPATH': str(plugin / 'src')})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
            await session.initialize()
            result = await session.call_tool('task_evidence_classify', {
                'project_id': project.project_id, 'arguments': request.model_dump()})
            response = result.structuredContent
            assert response and response['status'] == 'ok', result
            assert response['result']['classification'] == 'expected'
            assert not response['result']['admission_performed']
            assert response['tool_execution']['env_uop']['action_name'] == 'task_evidence_classify'
            assert response['tool_execution']['native_host_tool_attested'] is False

    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(exercise())
    assert project.pv_head() == before
