import json

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanStore, content_digest
from evidence_lane_plugin.project_memory import (
    MemoryCheckpoint,
    MemoryEdge,
    MemoryIngest,
    MemoryLocator,
    MemoryRead,
    MemoryReference,
    MemoryRehydrate,
    ProjectMemory,
    locator_identity,
)
from evidence_lane_plugin.sdk import ActionRequest
from pydantic import ValidationError

from tests.test_delta_entry import call, finished, plan, request, system
from tests.test_plan_replacement import apply, project, record_steer, replacement

__all__ = ['project', 'system']


def invoke(system, action, args):
    return call(system, ActionRequest(action=action, project_id=system[1].project_id, arguments=args))


def source(system, text='indexed evidence', profile='code'):
    lane_id = {'code': 'local_code', 'pdf': 'pdf_ocr'}[profile]
    digest = system[1].lane(lane_id, create=True).put_object(text.encode())
    return MemoryLocator(reference=MemoryReference(kind='source_object', key=digest, digest=digest, profile=profile, lane_id=lane_id),
                         label=text, search_terms=['evidence', 'continuity'])


def ingest(system, locators, edges=None, **options):
    req = MemoryIngest(locators=locators, edges=edges or [], **options)
    result = invoke(system, 'memory_ingest', req.model_dump())
    assert result.status == 'ok', result.error
    return result, req


def test_scoped_fts_filters_before_limit_and_read_only_empty_project(system, tmp_path):
    memory = ProjectMemory(system[1])
    before = system[1].database.read_bytes()
    assert memory.read().locators == []
    assert system[1].database.read_bytes() == before
    locators = [source(system, 'evidence alpha ' + str(i)) for i in range(30)]
    selected = source(system, 'evidence needle', 'pdf')
    ingest(system, [*locators, selected])
    before = system[1].database.read_bytes()
    page = invoke(system, 'memory_read', {'query': 'evidence OR "* needle', 'profile': 'pdf', 'limit': 1})
    assert page.status == 'ok', page.error
    assert len(page.result['locators']) == 1
    assert page.result['locators'][0]['locator']['label'] == 'evidence needle'
    assert page.result['locators'][0]['source_state'] == 'registered_content_reference'
    assert page.result['raw_source_payloads_returned'] is False
    assert system[1].database.read_bytes() == before
    assert memory.verify_history()['events_verified'] == 1
    entry = system[0].directory.register(tmp_path / 'other-state', source_root=system[1].source_root, create=True, read_only=False)
    other = system[0].directory.open(entry['project_id'], write=True)
    assert ProjectMemory(other).read().locators == []
    with system[0].project_work.mutation(other) as lease:
        with pytest.raises(LaneError) as error:
            ProjectMemory(other).ingest(MemoryIngest(locators=[selected]), lease, actor_id='fixture')
        assert error.value.code == 'MEMORY_SOURCE_MISMATCH'


def test_atomic_ingestion_replay_conflict_and_unknown_source_rollback(system):
    locator = source(system)
    first, req = ingest(system, [locator])
    again = invoke(system, 'memory_ingest', req.model_dump())
    assert again.result['duplicate'] is True
    assert again.result['head'] == first.result['head']
    changed = req.model_dump()
    changed['locators'][0]['label'] = 'different'
    assert invoke(system, 'memory_ingest', changed).error.code == 'MEMORY_REQUEST_CONFLICT'
    unknown = locator.model_copy(update={'reference': MemoryReference(kind='source_object', key='0'*64, digest='0'*64, profile='code', lane_id='local_code')})
    failing = MemoryIngest(locators=[source(system, 'must roll back'), unknown])
    assert invoke(system, 'memory_ingest', failing.model_dump()).error.code == 'MEMORY_SOURCE_MISMATCH'
    assert len(ProjectMemory(system[1]).read().locators) == 1
    assert ProjectMemory(system[1]).verify_history()['events_verified'] == 1


def test_typed_edges_suppress_only_memory_and_preserve_historical_attribution(system):
    plan(system)
    old, new = source(system, 'old evidence'), source(system, 'new evidence')
    old_id, new_id = [locator_identity(system[1].project_id, value) for value in (old, new)]
    edge = MemoryEdge(source_id=new_id, target_id=old_id, kind='SUPERSEDES', evidence=new.reference)
    before = PlanStore(system[1]).snapshot().model_dump()
    ingest(system, [old, new], [edge])
    page = ProjectMemory(system[1]).read(MemoryRead(query='evidence', limit=1))
    assert [row['locator_id'] for row in page.locators] == [new_id]
    assert page.locators[0]['edges'][0]['semantics_provenance'] == 'agent_report'
    history = ProjectMemory(system[1]).read(MemoryRead(include_history=True))
    assert len(history.locators) == 2
    assert next(item for item in history.locators if item['locator_id'] == old_id)['suppressed'] is True
    assert PlanStore(system[1]).snapshot().model_dump() == before
    assert ProjectMemory(system[1]).verify_history()['events_verified'] == 1


def test_owner_bound_plan_lineage_learning_and_receipt_references(system):
    view = plan(system)
    entered = call(system, request(system, view))
    finished(system, entered.job_id)
    with system[0].project_work.mutation(system[1]) as lease:
        lineage = ChatLineage(system[1]).append(LineageRecord(kind='assistant', payload={'text': 'visible result'}), lease, client_id='source-client')
    with system[1].lane('learning').connection(read_only=True) as connection, system[1].lane('receipts').connection(read_only=True) as receipts:
        lesson = connection.execute('SELECT * FROM learning_versions').fetchone()
        receipt = receipts.execute("SELECT * FROM receipts WHERE kind='delta_exit_verified'").fetchone()
    references = [MemoryReference(kind='plan_task', key='first', revision=1, digest=view.contract_digest),
        MemoryReference(kind='lineage_event', key=lineage.event_id, digest=lineage.cursor),
        MemoryReference(kind='learning_version', key=lesson['version_id'], digest=lesson['content_digest']),
        MemoryReference(kind='receipt', key=receipt['receipt_id'], digest=content_digest(json.loads(receipt['body_json'])))]
    ingest(system, [MemoryLocator(reference=ref, label=ref.kind) for ref in references])
    page = ProjectMemory(system[1]).read(MemoryRead(limit=20))
    assert len(page.locators) == 8
    assert sum(item['metadata_provenance'] == 'engine_verified_exit' for item in page.locators) == 4
    result = invoke(system, 'learning_revoke', {'version_id': lesson['version_id'], 'expected_digest': lesson['content_digest'], 'reason': 'No longer applicable.'})
    assert result.status == 'ok', result.error
    page = ProjectMemory(system[1]).read(MemoryRead(limit=20))
    assert len(page.locators) == 6
    assert all(item['locator']['reference']['kind'] != 'learning_version' for item in page.locators)
    historical = ProjectMemory(system[1]).read(MemoryRead(include_history=True))
    assert next(item for item in historical.locators if item['locator']['reference']['kind'] == 'learning_version')['source_state'] == 'revoked'
    bad = references[1].model_copy(update={'digest': 'f'*64})
    failed = invoke(system, 'memory_ingest', MemoryIngest(locators=[MemoryLocator(reference=bad, label='bad')]).model_dump())
    assert failed.error.code == 'MEMORY_SOURCE_MISMATCH'


def test_checkpoint_rehydrate_has_attribution_no_attachment_and_no_writes(system, tmp_path):
    view = plan(system)
    recorded, _ = ingest(system, [source(system)])
    checkpoint = MemoryCheckpoint(task_id='first', plan_revision=1, contract_digest=view.contract_digest, locator_ids=recorded.result['locator_ids'])
    sealed = invoke(system, 'memory_checkpoint', checkpoint.model_dump())
    assert sealed.status == 'ok', sealed.error
    assert invoke(system, 'memory_checkpoint', checkpoint.model_dump()).result == sealed.result
    before = system[1].database.read_bytes()
    read = invoke(system, 'memory_rehydrate', {'checkpoint_digest': sealed.result['checkpoint_digest']})
    assert read.status == 'ok', read.error
    assert read.result['plan_compatible'] and not read.result['memory_head_changed']
    assert read.result['checkpoint']['source_client_id'] == system[3].client_id
    assert read.result['native_task_attestation'] == 'not_provided' and read.result['host_session_attached'] is False
    assert system[1].database.read_bytes() == before
    ingest(system, [source(system, 'later material')])
    assert invoke(system, 'memory_rehydrate', {'checkpoint_digest': sealed.result['checkpoint_digest']}).result['memory_head_changed'] is True
    with system[0].project_work.mutation(system[1]) as lease:
        ChatLineage(system[1]).append(LineageRecord(kind='prompt', payload={'text': 'later visible question'}), lease, client_id='client')
    reread = invoke(system, 'memory_rehydrate', {'checkpoint_digest': sealed.result['checkpoint_digest']})
    assert reread.result['lineage_head_changed'] is True
    other = system[0].directory.register(tmp_path / 'other-state', source_root=system[1].source_root, create=True, read_only=False)
    with pytest.raises(LaneError) as error:
        ProjectMemory(system[0].directory.open(other['project_id'])).rehydrate(MemoryRehydrate(checkpoint_digest=sealed.result['checkpoint_digest']), receiver_client_id='other')
    assert error.value.code == 'MEMORY_CHECKPOINT_NOT_FOUND'


def test_plan_refresh_marks_checkpoint_historical_and_does_not_reopen_tasks(project):
    engine, plan_store, actor, _ = project
    view = plan_store.task('two', expected_revision=1)
    memory = ProjectMemory(plan_store.store)
    locator = MemoryLocator(reference=MemoryReference(kind='plan_task', key='two', revision=1, digest=view.contract_digest), label='Original task')
    with engine.project_work.mutation(plan_store.store) as lease:
        recorded = memory.ingest(MemoryIngest(locators=[locator]), lease, actor_id=actor)
        checkpoint = memory.checkpoint(MemoryCheckpoint(task_id='two', plan_revision=1, contract_digest=view.contract_digest, locator_ids=recorded.locator_ids), lease, actor_id=actor)
    steer = record_steer(project)
    apply(project, replacement(project, steer))
    before = plan_store.snapshot().model_dump()
    assert memory.read().locators == []
    assert memory.read(MemoryRead(include_history=True)).locators[0]['source_state'] == 'historical'
    recovered = memory.rehydrate(MemoryRehydrate(checkpoint_digest=checkpoint.checkpoint_digest), receiver_client_id='new-client')
    assert recovered.plan_compatible is False and recovered.current_plan_revision == 2
    assert recovered.checkpoint['source_client_id'] == actor
    assert plan_store.snapshot().model_dump() == before
    with engine.project_work.mutation(plan_store.store) as lease, pytest.raises(LaneError) as error:
        memory.checkpoint(MemoryCheckpoint(task_id='two', plan_revision=1, contract_digest=view.contract_digest, locator_ids=recorded.locator_ids), lease, actor_id=actor)
    assert error.value.code == 'MEMORY_CHECKPOINT_STALE_PLAN'


def test_delta_query_uses_memory_read_without_writes_and_cannot_wrap_ingest(system):
    plan(system)
    ingest(system, [source(system)])
    before = system[1].database.read_bytes()
    read = call(system, ActionRequest(action='delta_query', project_id=system[1].project_id, expected_revision=1,
        arguments={'task_id': 'first', 'plan_revision': 1, 'action': 'memory_read', 'arguments': {'query': 'evidence'}}))
    assert read.status == 'ok', read.error
    assert system[1].database.read_bytes() == before
    denied = call(system, ActionRequest(action='delta_query', project_id=system[1].project_id, expected_revision=1,
        arguments={'task_id': 'first', 'plan_revision': 1, 'action': 'memory_ingest', 'arguments': {}}))
    assert denied.status == 'error'


def test_bounds_secrets_private_fields_and_tampered_locator_are_rejected(system):
    ref = source(system).reference
    with pytest.raises(ValidationError):
        MemoryLocator(reference=ref, label='password=secret-value')
    with pytest.raises(ValidationError):
        MemoryLocator(reference=ref, label='one', private_reasoning='not visible')
    with pytest.raises(ValidationError):
        MemoryRead(limit=21)
    ingest(system, [MemoryLocator(reference=ref, label='one')])
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('memory') as connection:
        connection.execute("UPDATE memory_locators SET reference_key='wrong'")
    assert invoke(system, 'memory_read', {}).error.code == 'MEMORY_LOCATOR_INTEGRITY'


def test_verified_exit_refresh_is_observed_by_an_earlier_checkpoint(system):
    view = plan(system)
    checkpoint = invoke(system, 'memory_checkpoint', MemoryCheckpoint(
        task_id='first', plan_revision=1, contract_digest=view.contract_digest, locator_ids=[]).model_dump())
    assert checkpoint.status == 'ok', checkpoint.error
    args = {'checkpoint_digest': checkpoint.result['checkpoint_digest']}
    assert not invoke(system, 'memory_rehydrate', args).result['memory_head_changed']
    entered = call(system, request(system, view))
    assert finished(system, entered.job_id)['state'] == 'verified'
    before = {str(p): p.read_bytes() for p in system[1].root.rglob('*') if p.is_file()}
    result = invoke(system, 'memory_rehydrate', args)
    assert result.status == 'ok', result.error
    assert result.result['memory_head_changed'] is True
    assert result.result['plan_compatible'] is True
    assert result.result['host_session_attached'] is False
    assert {str(p): p.read_bytes() for p in system[1].root.rglob('*') if p.is_file()} == before


def test_plan_dependency_integrity_is_enforced_for_memory_reads_and_checkpoints(system):
    view = plan(system)
    locator = MemoryLocator(reference=MemoryReference(kind='plan_task', key='first',
        revision=1, digest=view.contract_digest), label='Bound task')
    recorded, _ = ingest(system, [locator])
    checkpoint = invoke(system, 'memory_checkpoint', MemoryCheckpoint(task_id='first', plan_revision=1,
        contract_digest=view.contract_digest, locator_ids=recorded.result['locator_ids']).model_dump())
    assert checkpoint.status == 'ok', checkpoint.error
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('plan') as connection:
        connection.execute("INSERT INTO plan_dependencies VALUES(1,'first','first')")
    before = {str(p): p.read_bytes() for p in system[1].root.rglob('*') if p.is_file()}
    for action, arguments in [('memory_read', {}), ('memory_rehydrate',
            {'checkpoint_digest': checkpoint.result['checkpoint_digest']})]:
        response = invoke(system, action, arguments)
        assert response.error is not None and response.error.code == 'MEMORY_SOURCE_INTEGRITY', response
    assert {str(p): p.read_bytes() for p in system[1].root.rglob('*') if p.is_file()} == before
