import asyncio
import hashlib
import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.project_memory import (
    MemoryCheckpoint,
    MemoryEdge,
    MemoryIngest,
    MemoryRead,
    MemoryRecordLink,
    MemoryReference,
    MemoryRehydrate,
    ProjectMemory,
    locator_identity,
)
from evidence_lane_plugin.storage import LaneStore
from evidence_lane_plugin.studio_gateway import StudioGateway
from pydantic import ValidationError

from .test_delta_entry import plan, system
from .test_native_workflow_bindings import native, projects
from .test_project_memory_v4 import invoke, source

__all__ = ['system']


def link_request(system, **changes):
    newer, older = source(system, 'new evidence'), source(system, 'old evidence')
    return MemoryRecordLink(source=newer, target=older, edge_type='SUPERSEDES',
        evidence=newer.reference).model_copy(update=changes)


def lane_bytes(store, lane_id):
    return {str(path.relative_to(store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.lane(lane_id).folder.rglob('*') if path.is_file()}


def test_one_call_creates_locators_link_and_receipt_with_exact_replay(system):
    request = link_request(system)
    before = lane_bytes(system[1], 'local_code')
    first = invoke(system, 'project_memory_record_link', request.model_dump())
    assert first.status == 'ok', first.error
    replay = invoke(system, 'project_memory_record_link', request.model_dump())
    assert replay.status == 'ok' and replay.result['duplicate'] is True
    assert replay.result['head'] == first.result['head']
    assert first.result['locator_ids'] == [locator_identity(system[1].project_id, item)
        for item in (request.source, request.target)]
    assert len(first.result['edge_ids']) == 1 and not first.result['source_authorities_mutated']
    memory = ProjectMemory(system[1])
    page = memory.read(MemoryRead(query='evidence'))
    assert len(page.locators) == 1 and page.locators[0]['locator']['label'] == 'new evidence'
    assert page.locators[0]['edges'][0]['semantics_provenance'] == 'agent_report'
    assert not page.raw_source_payloads_returned
    assert len(memory.read(MemoryRead(include_history=True)).locators) == 2
    assert memory.verify_history()['events_verified'] == 1
    with system[1].lane('receipts').connection(read_only=True) as connection:
        receipts = connection.execute("SELECT body_json FROM receipts WHERE kind='project_memory_record_link'").fetchall()
    assert len(receipts) == 1
    assert json.loads(receipts[0][0])['memory_head'] == first.result['head']
    assert lane_bytes(system[1], 'local_code') == before
    reused = invoke(system, 'project_memory_record_link', request.model_copy(update={'request_id': str(uuid4())}).model_dump())
    assert reused.status == 'ok' and reused.result['edge_ids'] == first.result['edge_ids']
    with system[1].lane('memory').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM memory_locators').fetchone()[0] == 2
        assert connection.execute('SELECT count(*) FROM memory_edges').fetchone()[0] == 1
    assert memory.verify_history()['events_verified'] == 2


def test_request_conflict_binds_content_client_and_action(system):
    request = link_request(system)
    assert invoke(system, 'project_memory_record_link', request.model_dump()).status == 'ok'
    changed = request.model_copy(update={'edge_type': 'RELATED_TO'})
    assert invoke(system, 'project_memory_record_link', changed.model_dump()).error.code == 'MEMORY_REQUEST_CONFLICT'
    edge = MemoryEdge(source_id=locator_identity(system[1].project_id, request.source),
        target_id=locator_identity(system[1].project_id, request.target), kind=request.edge_type, evidence=request.evidence)
    same_batch = MemoryIngest(request_id=request.request_id, locators=[request.source, request.target], edges=[edge])
    assert invoke(system, 'memory_ingest', same_batch.model_dump()).error.code == 'MEMORY_REQUEST_CONFLICT'
    with system[0].project_work.mutation(system[1]) as lease, pytest.raises(LaneError) as error:
        ProjectMemory(system[1]).record_link(request, lease, actor_id='different-client')
    assert error.value.code == 'MEMORY_REQUEST_CONFLICT'
    assert ProjectMemory(system[1]).verify_history()['events_verified'] == 1


@pytest.mark.parametrize('field', ['source', 'target', 'evidence'])
def test_unknown_or_stale_reference_rolls_back_entire_link(system, field):
    request = link_request(system)
    unknown = MemoryReference(kind='source_object', key='f' * 64, digest='f' * 64,
        lane_id='local_code', profile='code')
    changed = unknown if field == 'evidence' else getattr(request, field).model_copy(update={'reference': unknown})
    result = invoke(system, 'project_memory_record_link', request.model_copy(update={field: changed}).model_dump())
    assert result.status == 'error' and result.error.code == 'MEMORY_SOURCE_MISMATCH'
    memory = ProjectMemory(system[1])
    assert memory.read().locators == [] and memory.verify_history()['events_verified'] == 0


def test_link_cannot_import_foreign_project_references(system, tmp_path):
    request = link_request(system)
    entry = system[0].directory.register(tmp_path / 'other', source_root=system[1].source_root,
        create=True, read_only=False)
    other = system[0].directory.open(entry['project_id'], write=True)
    with system[0].project_work.mutation(other) as lease, pytest.raises(LaneError) as error:
        ProjectMemory(other).record_link(request, lease, actor_id=system[3].client_id)
    assert error.value.code == 'MEMORY_SOURCE_MISMATCH'
    assert ProjectMemory(other).read().locators == []


def test_receipt_failure_rolls_back_link_fts_and_event(system, monkeypatch):
    request = link_request(system)
    original = LaneStore.append_receipt

    def fail(self, kind, body, **kwargs):
        value = original(self, kind, body, **kwargs)
        if kind == 'project_memory_record_link':
            raise LaneError('INJECTED_MEMORY_RECEIPT_FAILURE', 'Fail after receipt registration.')
        return value

    monkeypatch.setattr(LaneStore, 'append_receipt', fail)
    result = invoke(system, 'project_memory_record_link', request.model_dump())
    assert result.error.code == 'INJECTED_MEMORY_RECEIPT_FAILURE'
    with system[1].lane('memory').connection(read_only=True) as connection:
        for name in ('memory_locators', 'memory_edges', 'memory_events', 'memory_fts'):
            assert connection.execute('SELECT count(*) FROM ' + name).fetchone()[0] == 0
    with system[1].lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='project_memory_record_link'").fetchone()[0] == 0


def test_link_changes_checkpoint_compatibility_observation(system):
    view = plan(system)
    checkpoint = invoke(system, 'memory_checkpoint', MemoryCheckpoint(task_id='first', plan_revision=1,
        contract_digest=view.contract_digest, locator_ids=[]).model_dump())
    assert checkpoint.status == 'ok', checkpoint.error
    request = MemoryRehydrate(checkpoint_digest=checkpoint.result['checkpoint_digest'])
    memory = ProjectMemory(system[1])
    assert not memory.rehydrate(request, receiver_client_id='reader').memory_head_changed
    assert invoke(system, 'project_memory_record_link', link_request(system).model_dump()).status == 'ok'
    observation = memory.rehydrate(request, receiver_client_id='reader')
    assert observation.memory_head_changed and observation.plan_compatible
    assert not observation.host_session_attached and not observation.source_authorities_mutated


def test_link_contract_rejects_self_link_secrets_and_untyped_payloads(system):
    data = link_request(system).model_dump()
    with pytest.raises(ValidationError):
        MemoryRecordLink.model_validate({**data, 'target': data['source']})
    with pytest.raises(ValidationError):
        MemoryRecordLink.model_validate({**data, 'evidence': {'arbitrary_hash': 'a' * 64}})
    with pytest.raises(ValidationError):
        MemoryRecordLink.model_validate({**data, 'source': {**data['source'], 'label': 'password=secret-value'}})
    with pytest.raises(ValidationError):
        MemoryRecordLink.model_validate({**data, 'private_reasoning': 'Excluded'})


def test_actual_mcp_link_and_read_only_studio(tmp_path):
    with Engine(tmp_path / 'runtime') as engine:
        store, _ = projects(engine, tmp_path)
        request = link_request((engine, store)).model_dump()
        with LocalEndpoint(engine, studio_enabled=False):
            async def exercise():
                async with native(engine.root, store.project_id, permissions=('read', 'write')) as session:
                    catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
                    assert catalog['project_memory_record_link'].annotations.readOnlyHint is False
                    result = await session.call_tool('project_memory_record_link',
                        {'project_id': store.project_id, 'arguments': request})
                    assert not result.isError and result.structuredContent['status'] == 'ok', result
                    assert json.loads(result.content[0].text) == result.structuredContent
                async with native(engine.root, store.project_id) as readonly:
                    denied = await readonly.call_tool('project_memory_record_link',
                        {'project_id': store.project_id, 'arguments': request})
                    assert denied.isError and denied.structuredContent['error']['code'] == 'PROJECT_NOT_SELECTED'
            asyncio.run(exercise())
        gateway = StudioGateway(engine)
        _, studio = gateway.exchange(gateway.issue_ticket())
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in store.root.rglob('*') if path.is_file()}
        observed = gateway.command('read', {'project_id': store.project_id,
            'action': 'memory_read', 'arguments': {'query': 'evidence'}}, studio)
        assert len(observed['locators']) == 1, observed
        with pytest.raises(LaneError) as error:
            gateway.command('read', {'project_id': store.project_id,
                'action': 'project_memory_record_link', 'arguments': request}, studio)
        assert error.value.code == 'NOT_A_STUDIO_QUERY'
        assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in store.root.rglob('*') if path.is_file()} == before
