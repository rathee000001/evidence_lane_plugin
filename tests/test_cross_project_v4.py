import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher, dispatch_authenticated
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.project_universe import ProjectLink, ProjectUniverse, ProjectUnlink
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import LaneStore, ProjectStore, bounded_project_read


@pytest.fixture
def projects(tmp_path):
    with Engine(tmp_path / 'runtime') as engine:
        stores = []
        for name in ('one', 'two'):
            source = tmp_path / (name + '-source')
            source.mkdir()
            (source / 'identical-name.txt').write_text(name)
            entry = engine.directory.register(tmp_path / (name + '-state'), source_root=source, create=True, read_only=False)
            store = engine.directory.open(entry['project_id'], write=True)
            with engine.project_work.mutation(store) as lease:
                PlanStore(store).create(PlanCreate(title=name, tasks=[TaskDefinition(task_id='same-task', title=name, requested_outcome='Keep this project separate')]), lease, actor_id='fixture')
                ChatLineage(store).append(LineageRecord(kind='prompt', payload={'text': 'Only in project ' + name}), lease, client_id='fixture')
            stores.append(store)
        token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id, permissions=['read', 'write']) for store in stores]))
        def call(action, arguments=None, *, actor=None, source=None):
            payload = arguments.model_dump(mode='json') if hasattr(arguments, 'model_dump') else arguments or {}
            return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=(source or stores[0]).project_id, arguments=payload), actor or session)
        yield engine, stores, token, session, call


def query(stores, *actions, **changes):
    data = {'projects': [{'project_id': store.project_id, 'queries': [{'action': action} for action in actions or ('plan_read',)]} for store in stores]}
    data.update(changes)
    return data


def checksums(stores):
    return [{str(path.relative_to(store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in store.root.rglob('*') if path.is_file() and path.suffix not in {'.lock', '.db-shm'} and not path.name.endswith(('-shm', '-wal'))} for store in stores]


def test_attributed_reads_preserve_separate_databases_and_sources(projects):
    _engine, stores, _, _, call = projects
    before = checksums(stores)
    result = call('linked_project_evidence_query', query(stores, 'plan_read', 'lineage_read', 'memory_read'))
    assert result.status == 'ok', result.error
    page = result.result
    assert page['databases_merged'] is page['mutation_performed'] is page['refresh_performed'] is False
    assert [item['queries'][0]['result']['title'] for item in page['projects']] == ['one', 'two']
    assert [item['queries'][1]['result']['events'][0]['payload']['text'] for item in page['projects']] == ['Only in project one', 'Only in project two']
    assert all(item['format_version'] == 4 and item['plan_boundary']['revision'] == 1 for item in page['projects'])
    assert checksums(stores) == before
    assert stores[0].root != stores[1].root


def test_link_only_changes_source_and_never_grants_target_access(projects):
    engine, stores, _, _, call = projects
    before_target = checksums([stores[1]])
    request = ProjectLink(target_project_id=stores[1].project_id, label='Reference project')
    linked = call('project_evidence_link', request)
    assert linked.status == 'ok', linked.error
    assert linked.result['access_granted'] is linked.result['target_mutated'] is False
    assert call('project_evidence_link', request).result == linked.result
    verified = call('project_evidence_links_verify')
    assert verified.status == 'ok' and verified.result['events_verified'] == 1
    assert checksums([stores[1]]) == before_target
    assert call('linked_project_evidence_query', query(stores, require_links=True)).status == 'ok'
    _, source_only = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=stores[0].project_id)]))
    assert call('project_evidence_links_read', actor=source_only).result['links'][0]['label'] == 'Reference project'
    assert call('linked_project_evidence_query', query(stores, require_links=True), actor=source_only).error.code == 'PROJECT_NOT_SELECTED'
    removed = call('project_evidence_unlink', ProjectUnlink(target_project_id=stores[1].project_id, expected_version=1))
    assert removed.status == 'ok'
    assert call('project_evidence_links_verify').result['events_verified'] == 2
    assert call('linked_project_evidence_query', query(stores, require_links=True)).error.code == 'PROJECT_LINK_REQUIRED'
    assert call('linked_project_evidence_query', query(stores)).status == 'ok'
    assert checksums([stores[1]]) == before_target


def test_link_version_conflict_and_receipt_failure_are_atomic(projects, monkeypatch):
    _engine, stores, _, _, call = projects
    request = ProjectLink(target_project_id=stores[1].project_id, label='Reference project')
    assert call('project_evidence_link', request).status == 'ok'
    assert call('project_evidence_link', ProjectLink(target_project_id=stores[1].project_id, label='Wrong version')).error.code == 'PROJECT_LINK_VERSION_CONFLICT'
    prior = ProjectUniverse(stores[0]).read().model_dump()
    prior_lane = hashlib.sha256(stores[0].lane('universe').database.read_bytes()).hexdigest()
    target_before = checksums([stores[1]])
    original = LaneStore.append_receipt
    injected = []
    def fail(self, kind, *args, **kwargs):
        if kind == 'project_link_changed':
            injected.append(self.lane_id)
            raise RuntimeError('Injected receipt failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(LaneStore, 'append_receipt', fail)
    response = call('project_evidence_unlink', ProjectUnlink(target_project_id=stores[1].project_id, expected_version=1))
    assert injected == ['universe']
    assert response.status == 'error' and response.error.code == 'TOOL_ADAPTER_FAILED'
    assert ProjectUniverse(stores[0]).read().model_dump() == prior
    assert hashlib.sha256(stores[0].lane('universe').database.read_bytes()).hexdigest() == prior_lane
    assert checksums([stores[1]]) == target_before


def test_revoked_source_cannot_link_after_acquiring_writer(projects, monkeypatch):
    from contextlib import contextmanager
    engine, stores, token, _, call = projects
    original = engine.project_work.mutation
    @contextmanager
    def revoke_before_work(store, *args, **kwargs):
        with original(store, *args, **kwargs) as lease:
            engine.clients.disconnect(token)
            yield lease
    monkeypatch.setattr(engine.project_work, 'mutation', revoke_before_work)
    result = call('project_evidence_link', ProjectLink(target_project_id=stores[1].project_id, label='Must not link'))
    assert result.error.code == 'CLIENT_SESSION_EXPIRED'
    assert ProjectUniverse(stores[0]).read().links == []


@pytest.mark.parametrize('action', ['plan_create', 'linked_project_evidence_query', 'delta_enter'])
def test_mutation_and_recursive_queries_are_rejected(projects, action):
    _, stores, _, _, call = projects
    before = checksums(stores)
    assert call('linked_project_evidence_query', query(stores, action)).error.code == 'NOT_A_CROSS_PROJECT_QUERY'
    assert checksums(stores) == before


def test_missing_transport_authority_cannot_fabricate_target_context(projects):
    engine, stores, _, session, _ = projects
    context = ActionContext(session.client_id, stores[0].project_id, frozenset({'read'}))
    result = dispatch_authenticated(engine, ActionRequest(action='linked_project_evidence_query', project_id=stores[0].project_id, arguments=query(stores)), context)
    assert result.error.code == 'CROSS_PROJECT_AUTHORIZATION_UNAVAILABLE'


@pytest.mark.parametrize(('sql', 'expected'), [
    ("UPDATE project SET format_version=5", 'UNSUPPORTED_PROJECT_VERSION'),
    ("UPDATE schema_migrations SET version=99 WHERE owner='plan' AND version=2", 'SCHEMA_NEWER_THAN_ENGINE'),
    ("UPDATE schema_migrations SET digest='bad' WHERE owner='lineage'", 'QUERY_SCHEMA_INCOMPATIBLE'),
    ("DELETE FROM schema_migrations WHERE owner='lineage'", 'QUERY_SCHEMA_METADATA_MISSING'),
    ("DELETE FROM schema_ownership WHERE object_name='lineage_events'", 'QUERY_SCHEMA_OWNERSHIP'),
])
def test_incompatible_targets_fail_without_automatic_migration(projects, sql, expected):
    _, stores, _, _, call = projects
    if expected == 'SCHEMA_NEWER_THAN_ENGINE':
        from evidence_lane_plugin.migrations import Migration, apply_migrations
        from evidence_lane_plugin.plan_runtime import PLAN_MIGRATIONS
        future_version = PLAN_MIGRATIONS[-1].version + 1
        apply_migrations(stores[1], (*PLAN_MIGRATIONS, Migration('plan', future_version, 'Future compatible owner',
            ('CREATE TABLE plan_future(value TEXT)',))))
    else:
        target = stores[1] if expected == 'UNSUPPORTED_PROJECT_VERSION' else stores[1].lane('chat_lineage')
        with target.transaction() as connection:
            if expected == 'QUERY_SCHEMA_METADATA_MISSING':
                connection.execute("DELETE FROM schema_history_files WHERE owner='lineage'")
            connection.execute(sql)
    before = checksums(stores)
    result = call('linked_project_evidence_query', query(stores, 'lineage_read'))
    assert result.error.code == expected
    assert checksums(stores) == before


def test_absent_owners_stay_absent_and_unrelated_future_owner_is_ignored(projects, tmp_path):
    engine, stores, _, _, call = projects
    source = tmp_path / 'empty-source'
    source.mkdir()
    target = ProjectStore.create(tmp_path / 'empty-state', source)
    engine.directory.register(target.root, read_only=True)
    _, reader = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=item.project_id) for item in (stores[0], target)]))
    with stores[0].lane('plan').transaction() as connection:
        connection.execute("INSERT INTO schema_migrations VALUES('unrelated',999,'unrelated','Unrelated future owner','fixture')")
    before = checksums([target])
    result = call('linked_project_evidence_query', query([stores[0], target], 'plan_read', 'memory_read'), actor=reader)
    assert result.status == 'ok', result.error
    assert result.result['projects'][1]['queries'][0]['result']['state'] == 'no_plan'
    assert checksums([target]) == before


def test_stale_revision_limits_and_connection_action_scope_fail(projects):
    engine, stores, _, session, call = projects
    request = query(stores)
    request['projects'][1]['expected_plan_revision'] = 2
    assert call('linked_project_evidence_query', request).error.code == 'QUERY_PLAN_REVISION_MISMATCH'
    assert call('linked_project_evidence_query', query([stores[0], stores[0]])).error.code == 'INVALID_ARGUMENTS'
    assert call('linked_project_evidence_query', query(stores, max_bytes=2048)).error.code == 'QUERY_OUTPUT_BUDGET'
    context = replace(engine.clients.context(session, stores[0].project_id, 'read'), allowed_actions=frozenset({'linked_project_evidence_query'}))
    assert dispatch_authenticated(engine, ActionRequest(action='linked_project_evidence_query', project_id=stores[0].project_id, arguments=query(stores)), context).error.code == 'ACTION_SCOPE_DENIED'


def test_revocation_during_later_read_suppresses_all_results(projects, monkeypatch):
    engine, stores, token, _, call = projects
    original = PlanStore.snapshot
    def revoke(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.store.project_id == stores[1].project_id:
            # A real independent owner operation while this read is in flight.
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(engine.clients.disconnect, token).result(timeout=5)
        return result
    monkeypatch.setattr(PlanStore, 'snapshot', revoke)
    result = call('linked_project_evidence_query', query(stores))
    assert result.status == 'error' and result.result is None
    assert result.error.code == 'CLIENT_SESSION_EXPIRED'


def test_earlier_plan_change_during_later_project_read_rejects_batch(projects, monkeypatch):
    engine, stores, _, _, call = projects
    original = PlanStore.snapshot
    def change(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.store.project_id == stores[1].project_id:
            def external_writer():
                with engine.project_work.mutation(stores[0]) as lease, lease.transaction('plan') as connection:
                    # Emulates a separately committed Plan event while the
                    # second project is read; query must discard the batch.
                    connection.execute("UPDATE plan_events SET digest=? WHERE sequence=1", ('f' * 64,))
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(external_writer).result(timeout=5)
        return result
    monkeypatch.setattr(PlanStore, 'snapshot', change)
    result = call('linked_project_evidence_query', query(stores))
    assert result.status == 'error' and result.result is None
    assert result.error.code == 'QUERY_PLAN_CHANGED'


def test_enclosing_query_budget_cannot_be_disabled_and_writes_are_rejected(projects):
    _, stores, _, _, _ = projects
    before = checksums(stores)
    with bounded_project_read(stores[0].root, time.monotonic() + 0.05):
        with pytest.raises(LaneError) as error, stores[0].connection(read_only=True) as connection:
            connection.set_progress_handler(None, 0)
            connection.execute('WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<100000000) SELECT sum(n) FROM numbers').fetchone()
        assert error.value.code == 'QUERY_TIMEOUT'
    with bounded_project_read(stores[0].root, time.monotonic() + 3):
        with pytest.raises(LaneError):
            stores[0].put_object(b'Do not write')
        with pytest.raises(LaneError), stores[1].connection(read_only=True):
            pass
        with pytest.raises(LaneError), stores[0].transaction():
            pass
        with pytest.raises(LaneError):
            ProjectStore.create(stores[0].root.parent / 'must-not-create', stores[0].source_root)
        with pytest.raises(LaneError):
            stores[0].append_receipt('must-not-record', {})
    assert checksums(stores) == before
