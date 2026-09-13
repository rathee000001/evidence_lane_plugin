"""Adapt the original federation reuse/grant tests to real isolated project owners."""
from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher, dispatch_authenticated
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import LaneStore
from evidence_lane_plugin.universe_snapshot import inspect_project


def hashes(project):
    return {str(path.relative_to(project.root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in project.root.rglob('*') if path.is_file() and not path.name.endswith(('.lock', '-shm', '-wal'))}


@pytest.fixture
def federation(tmp_path):
    with Engine(tmp_path / 'engine') as engine:
        projects = []
        for name in ('coordinator', 'member-one', 'member-two'):
            source = tmp_path / (name + '-source')
            source.mkdir()
            entry = engine.directory.register(tmp_path / (name + '-state'), source_root=source, create=True, read_only=False)
            project = engine.directory.open(entry['project_id'], write=True)
            if name != 'coordinator':
                with engine.project_work.mutation(project) as lease:
                    PlanStore(project).create(PlanCreate(title=name, tasks=[TaskDefinition(task_id='first', title=name,
                        requested_outcome='Keep every project separate')]), lease, actor_id='fixture')
            projects.append(project)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id,
            permissions=['read', 'write']) for project in projects]))

        def call(action, arguments=None, *, project=None, actor=None):
            return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action,
                project_id=(project or projects[0]).project_id, arguments=arguments or {}), actor or session)

        yield engine, projects, session, call


def create_and_register(federation):
    _, projects, _, call = federation
    created = call('project_evidence_network_create', {'name': 'Selected research federation'})
    assert created.status == 'ok', created.error
    records = []
    for project in projects[1:]:
        snapshot = call('project_evidence_map_inspect', project=project)
        assert snapshot.status == 'ok', snapshot.error
        request = {'target_project_id': project.project_id, 'expected_version': 0,
                   'expected_snapshot_sha256': snapshot.result['snapshot_sha256']}
        registered = call('project_evidence_network_register', request)
        assert registered.status == 'ok', registered.error
        records.append((request, registered.result['result']['member']))
    return records


def grant_link(federation, records):
    *_, call = federation
    result = call('project_evidence_network_grant', {'source_mini_brain_id': records[0][1]['lane_heads']['plan'],
        'target_mini_brain_id': records[1][1]['lane_heads']['plan'], 'relation': 'supports'})
    assert result.status == 'ok', result.error
    request = {'grant_id': result.result['result']['grant_id'], 'evidence': {'relation_evidence_sha256': 'c' * 64}}
    return request


def test_register_reuse_retains_history_and_never_changes_members(federation):
    engine, projects, _, call = federation
    before = [hashes(project) for project in projects[1:]]
    records = create_and_register(federation)
    assert [hashes(project) for project in projects[1:]] == before
    coordinator_before = hashes(projects[0])
    for request, member in records:
        result = call('project_evidence_network_register', request)
        assert result.result['writes_performed'] is False
        assert result.result['result']['member'] == member
    assert hashes(projects[0]) == coordinator_before
    request, old_member = records[0]
    with engine.project_work.mutation(projects[1]) as lease:
        ChatLineage(projects[1]).append(LineageRecord(kind='prompt', payload={'text': 'Private content stays here'}), lease, client_id='fixture')
    current = inspect_project(projects[1])
    advanced = call('project_evidence_network_register', {**request, 'expected_version': 1, 'expected_snapshot_sha256': current.snapshot_sha256})
    assert advanced.status == 'ok', advanced.error
    new_member = advanced.result['result']['member']
    assert new_member['lane_heads']['plan'] == old_member['lane_heads']['plan']
    assert new_member['lane_heads']['chat_lineage'] != old_member['lane_heads']['chat_lineage']
    assert advanced.result['result']['changed_lane_ids'] == ['chat_lineage', 'receipts']
    with projects[0].lane('universe').connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM federation_member_history').fetchone()[0] == 3
        assert connection.execute('SELECT COUNT(*) FROM federation_mini_brains').fetchone()[0] == 20
    assert b'Private content stays here' not in projects[0].lane('universe').database.read_bytes()
    verified = call('project_evidence_network_verify')
    assert verified.status == 'ok', verified.error
    assert verified.result['result']['member_current_state_verified'] is False


def test_exact_grants_hash_only_edges_reuse_and_revocation(federation):
    _, projects, _, call = federation
    records = create_and_register(federation)
    request = grant_link(federation, records)
    before_members = [hashes(project) for project in projects[1:]]
    rejected = call('project_evidence_network_link', {**request, 'evidence': {'raw_text': 'never copy this'}})
    assert rejected.error.code == 'INVALID_ARGUMENTS'
    linked = call('project_evidence_network_link', request)
    assert linked.status == 'ok', linked.error
    before_reuse = hashes(projects[0])
    reused = call('project_evidence_network_link', request)
    assert reused.result['writes_performed'] is False
    assert reused.result['result'] == linked.result['result']
    assert hashes(projects[0]) == before_reuse
    revoked = call('project_evidence_network_revoke', {'grant_id': request['grant_id']})
    assert revoked.status == 'ok'
    assert call('project_evidence_network_link', request).error.code == 'FEDERATION_GRANT_INACTIVE'
    assert call('project_evidence_network_read', {'view': 'links'}).result['result']['records'][0] == linked.result['result']
    assert call('project_evidence_network_verify').status == 'ok'
    assert [hashes(project) for project in projects[1:]] == before_members


def test_member_selection_is_required_even_for_existing_edge(federation):
    engine, projects, _, call = federation
    records = create_and_register(federation)
    request = grant_link(federation, records)
    assert call('project_evidence_network_link', request).status == 'ok'
    _, owner_only = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=projects[0].project_id,
        permissions=['read', 'write'])]))
    assert call('project_evidence_network_register', records[0][0], actor=owner_only).error.code == 'PROJECT_NOT_SELECTED'
    assert call('project_evidence_network_link', request, actor=owner_only).error.code == 'PROJECT_NOT_SELECTED'
    # Stored hashes remain available to the coordinator, with no live access claim.
    result = call('project_evidence_network_read', actor=owner_only)
    assert result.status == 'ok' and result.result['result']['member_live_access_checked'] is False
    forged = ActionContext('invented', projects[0].project_id, frozenset({'read', 'write'}))
    result = dispatch_authenticated(engine, ActionRequest(action='project_evidence_network_register', project_id=projects[0].project_id,
        arguments=records[0][0]), forged)
    assert result.error.code == 'CROSS_PROJECT_AUTHORIZATION_UNAVAILABLE'


def test_stale_snapshots_wrong_versions_and_self_registration_fail(federation):
    engine, projects, _, call = federation
    records = create_and_register(federation)
    request, _ = records[0]
    with engine.project_work.mutation(projects[1]) as lease:
        ChatLineage(projects[1]).append(LineageRecord(kind='prompt', payload={'text': 'Change'}), lease, client_id='fixture')
    assert call('project_evidence_network_register', request).error.code == 'FEDERATION_SNAPSHOT_CHANGED'
    current = inspect_project(projects[1])
    assert call('project_evidence_network_register', {**request, 'expected_snapshot_sha256': current.snapshot_sha256}).error.code == 'FEDERATION_VERSION_CONFLICT'
    assert call('project_evidence_network_register', {**request, 'target_project_id': projects[0].project_id}).error.code == 'FEDERATION_MEMBER_SCOPE'
    assert call('project_evidence_network_register', {**request, 'inspection': {'verify_files': False}}).error.code == 'INVALID_ARGUMENTS'


def test_federation_read_budget_action_scope_and_receipt_failure(federation, monkeypatch):
    engine, projects, session, call = federation
    records = create_and_register(federation)
    assert call('project_evidence_network_verify', {'max_records': 1}).error.code == 'FEDERATION_VERIFY_BUDGET'
    first = call('project_evidence_network_read', {'view': 'mini_brains', 'limit': 1}).result['result']
    second = call('project_evidence_network_read', {'view': 'mini_brains', 'limit': 1, 'after_id': first['last_id']}).result['result']
    assert first['truncated'] and first['last_id'] != second['last_id']
    restricted = replace(engine.clients.context(session, projects[0].project_id, 'read'), allowed_actions=frozenset({'project_evidence_network_read'}))
    denied = dispatch_authenticated(engine, ActionRequest(action='project_evidence_network_grant', project_id=projects[0].project_id,
        arguments={'source_mini_brain_id': records[0][1]['lane_heads']['plan'],
                   'target_mini_brain_id': records[1][1]['lane_heads']['plan'], 'relation': 'supports'}), restricted)
    assert denied.error.code == 'ACTION_SCOPE_DENIED'
    request = grant_link(federation, records)
    original = LaneStore.append_receipt
    injected = []
    before_universe = projects[0].lane('universe').database.read_bytes()
    def fail(self, kind, *args, **kwargs):
        if kind == 'federation_edge_linked':
            injected.append(self.lane_id)
            raise RuntimeError('Injected federation receipt failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(LaneStore, 'append_receipt', fail)
    failed = call('project_evidence_network_link', request)
    assert injected == ['universe']
    assert failed.status == 'error' and failed.error.code == 'TOOL_ADAPTER_FAILED'
    assert projects[0].lane('universe').database.read_bytes() == before_universe
    assert call('project_evidence_network_read', {'view': 'links'}).result['result']['records'] == []


def test_universe_inspection_graph_and_budgets_are_read_only(federation):
    _, projects, _, call = federation
    before = hashes(projects[1])
    snapshot = call('project_evidence_map_inspect', project=projects[1])
    assert snapshot.status == 'ok', snapshot.error
    assert len(snapshot.result['lanes']) == 9
    graph = call('project_evidence_map_query', project=projects[1])
    assert graph.status == 'ok', graph.error
    assert {'project', 'root_pv', 'lane', 'plan_task'} <= {node['kind'] for node in graph.result['graph']['nodes']}
    assert 'DEPENDS_ON' not in {edge['kind'] for edge in graph.result['graph']['edges']}
    small = call('project_evidence_map_query', {'node_limit': 1}, project=projects[1])
    assert small.result['graph']['truncated'] and small.result['source_offsets']['next_task_offset'] == 0
    assert call('project_evidence_map_inspect', {'max_files': 1}, project=projects[1]).error.code == 'UNIVERSE_FILE_BUDGET'
    assert call('project_evidence_map_inspect', {'max_bytes': 1024}, project=projects[1]).error.code == 'UNIVERSE_BYTE_BUDGET'
    assert hashes(projects[1]) == before


def test_root_and_registered_object_tampering_are_visible(federation):
    engine, projects, _, call = federation
    target = projects[1]
    with engine.project_work.mutation(target) as lease, lease.transaction('memory'):
        value = target.lane('memory').put_object(b'Exact immutable content')
    path = target.lane('memory').object_path(value)
    path.write_bytes(b'Changed')
    assert call('project_evidence_map_inspect', project=target).error.code == 'UNIVERSE_FILE_INTEGRITY'
    # Deliberate tampering only in this disposable fixture.
    path.write_bytes(b'Exact immutable content')
    with target._raw_connection(read_only=False) as connection:
        connection.execute("UPDATE root_pv_head SET head_digest=?", ('f' * 64,))
        connection.commit()
    assert call('project_evidence_map_inspect', project=target).error.code == 'UNIVERSE_ROOT_INTEGRITY'


def test_federation_graph_preserves_parallel_relations_and_historical_references(federation):
    engine, projects, _, call = federation
    records = create_and_register(federation)
    request = grant_link(federation, records)
    assert call('project_evidence_network_link', request).status == 'ok'
    second = call('project_evidence_network_grant', {'source_mini_brain_id': records[0][1]['lane_heads']['plan'],
        'target_mini_brain_id': records[1][1]['lane_heads']['plan'], 'relation': 'contradicts'})
    assert second.status == 'ok', second.error
    assert call('project_evidence_network_link', {**request, 'grant_id': second.result['result']['grant_id']}).status == 'ok'
    preview = call('lane_view_preview', {'view_id': 'universe.federation', 'scope': {'include_history': True}})
    assert preview.status == 'ok', preview.error
    graph = preview.result['graph']
    assert {node['label'] for node in graph['nodes'] if node['kind'] == 'federation_edge'} == {'supports', 'contradicts'}
    assert len([edge for edge in graph['edges'] if edge['kind'] == 'HASH_SOURCE']) == 2
    with engine.project_work.mutation(projects[1]) as lease:
        PlanStore(projects[1]).transition('first', 'active', lease, expected_revision=1, actor_id='fixture')
    snapshot = inspect_project(projects[1])
    assert call('project_evidence_network_register', {**records[0][0], 'expected_version': 1,
        'expected_snapshot_sha256': snapshot.snapshot_sha256}).status == 'ok'
    historical = call('lane_view_preview', {'view_id': 'universe.federation', 'scope': {'include_history': True}})
    assert len([node for node in historical.result['graph']['nodes'] if node['kind'] == 'federation_edge']) == 2
    assert call('project_evidence_network_verify').status == 'ok'


def test_expired_grant_cannot_create_an_edge(federation, monkeypatch):
    import evidence_lane_plugin.universe_federation as module
    *_, call = federation
    records = create_and_register(federation)
    request = grant_link(federation, records)
    later = datetime.now(UTC) + timedelta(hours=2)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return later
    monkeypatch.setattr(module, 'datetime', Later)
    assert call('project_evidence_network_link', request).error.code == 'FEDERATION_GRANT_INACTIVE'
    assert call('project_evidence_network_read', {'view': 'links'}).result['result']['records'] == []


def test_historical_reference_tampering_fails_record_verification(federation):
    from evidence_lane_plugin.storage import json_text
    from evidence_lane_plugin.universe_snapshot import digest
    engine, projects, _, call = federation
    records = create_and_register(federation)
    with engine.project_work.mutation(projects[0]) as lease, lease.transaction('universe') as connection:
        # Deliberate corrupt metadata rehashed by the fixture still cannot
        # turn an invented history into the actual captured member snapshot.
        body = {**records[0][1], 'snapshot_sha256': '0' * 64}
        connection.execute('UPDATE federation_member_history SET body_json=?,digest=? WHERE project_id=?',
            (json_text(body), digest(body), projects[1].project_id))
    assert call('project_evidence_network_verify').error.code == 'FEDERATION_INTEGRITY'


def test_packaged_mcp_routes_real_federation_and_universe_actions(federation, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    engine, projects, _, _ = federation
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    arguments = ['-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                 '--host-profile', 'codex_desktop', '--permission', 'read', '--permission', 'write']
    for project in projects:
        arguments += ['--project-id', project.project_id]
    parameters = StdioServerParameters(command=sys.executable, args=arguments,
        env={'PYTHONPATH': str(plugin / 'src'), 'EVIDENCE_LANE_STUDIO_ROOT': str(tmp_path / 'uninstalled-studio')})
    async def run():
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session):
            await session.initialize()
            async def action(action_name, project, **args):
                result = await session.call_tool(action_name, {'project_id': project.project_id, 'arguments': args})
                value = result.structuredContent
                assert value and value['status'] == 'ok', result
                return value['result']
            await action('project_evidence_network_create', projects[0], name='MCP selected federation')
            before = [hashes(project) for project in projects[1:]]
            filtered = await action('project_evidence_map_query', projects[1], node_kind='plan_task', query='FIRST', node_limit=1)
            assert [node['key'] for node in filtered['graph']['nodes']] == ['first']
            assert filtered['source_offsets']['next_task_offset'] is None
            members = []
            for project in projects[1:]:
                snapshot = await action('project_evidence_map_inspect', project)
                result = await action('project_evidence_network_register', projects[0], target_project_id=project.project_id,
                    expected_snapshot_sha256=snapshot['snapshot_sha256'])
                members.append(result['result']['member'])
            grant = await action('project_evidence_network_grant', projects[0],
                source_mini_brain_id=members[0]['lane_heads']['plan'], target_mini_brain_id=members[1]['lane_heads']['plan'], relation='supports')
            await action('project_evidence_network_link', projects[0], grant_id=grant['result']['grant_id'], evidence={'review_sha256': 'd' * 64})
            verified = await action('project_evidence_network_verify', projects[0])
            assert verified['state'] == 'verified'
            view = await action('lane_view_preview', projects[0], view_id='universe.federation')
            assert any(node['kind'] == 'federation_edge' for node in view['graph']['nodes'])
            assert [hashes(project) for project in projects[1:]] == before
    with LocalEndpoint(engine):
        asyncio.run(run())


def test_filtered_universe_pages_advance_past_nonmatches_without_losing_hits(federation):
    engine, projects, _, call = federation
    project = projects[0]
    with engine.project_work.mutation(project) as lease:
        PlanStore(project).create(PlanCreate(title='Search fixture', tasks=[TaskDefinition(
            task_id=f'task-{number:03d}', title=('Report_100%' if number >= 101 else 'Other work'),
            requested_outcome='Find selected tasks') for number in range(103)]), lease, actor_id='fixture')
    before = hashes(project)
    request = {'node_kind': 'plan_task', 'query': 'REPORT_100%', 'node_limit': 1}
    empty = call('project_evidence_map_query', request).result
    assert empty['graph']['nodes'] == [] and empty['graph']['truncated']
    assert empty['source_offsets']['next_task_offset'] == 100
    first = call('project_evidence_map_query', {**request, 'task_offset': 100}).result
    assert [node['key'] for node in first['graph']['nodes']] == ['task-101']
    assert first['source_offsets']['next_task_offset'] == 102
    last = call('project_evidence_map_query', {**request, 'task_offset': 102}).result
    assert [node['key'] for node in last['graph']['nodes']] == ['task-102']
    assert last['source_offsets']['next_task_offset'] is None and not last['graph']['truncated']
    assert first['root_pv'] == empty['root_pv'] == last['root_pv']
    assert call('project_evidence_map_query', {**request, 'query': 'ReportX100Y'}).result['graph']['nodes'] == []
    assert hashes(project) == before


def test_filtered_lane_and_link_cursors_preserve_all_matches_and_link_history(federation):
    _, projects, _, call = federation
    before_members = [hashes(project) for project in projects[1:]]
    for project in projects[1:]:
        assert call('project_evidence_link', {'target_project_id': project.project_id, 'label': 'Related project'}).status == 'ok'
    before = hashes(projects[0])
    cursor, seen = 0, []
    for _ in range(30):
        page = call('project_evidence_map_query', {'node_kind': 'lane', 'node_limit': 1, 'topology_offset': cursor}).result
        seen.extend(node['key'] for node in page['graph']['nodes'])
        cursor = page['source_offsets']['next_topology_offset']
        if cursor is None:
            break
    assert seen == [row['lane_id'] for row in projects[0].lane_catalog()]
    request = {'node_kind': 'linked_project', 'node_limit': 1}
    first = call('project_evidence_map_query', request).result
    cursor = first['source_offsets']['next_linked_project_after']
    last = call('project_evidence_map_query', {**request, 'linked_project_after': cursor}).result
    assert first['graph']['nodes'][0]['key'] != last['graph']['nodes'][0]['key']
    assert last['source_offsets']['next_linked_project_after'] is None and not last['graph']['truncated']
    assert hashes(projects[0]) == before
    assert call('project_evidence_unlink', {'target_project_id': projects[1].project_id, 'expected_version': 1}).status == 'ok'
    verified = call('project_evidence_links_verify')
    assert verified.status == 'ok' and verified.result['events_verified'] == 3
    assert not verified.result['target_current_state_verified']
    assert [hashes(project) for project in projects[1:]] == before_members


def test_filtered_sources_use_owner_locators_without_copying_payloads(federation):
    from evidence_lane_plugin.source_authority import SourceAuthoritySpec, register_source_batch
    _, projects, _, call = federation
    project = projects[0]
    specs = []
    for ordinal, name in enumerate(('report_100%.txt', 'report_100%-other.txt', 'unrelated.txt'), 1):
        path = project.source_root / name
        path.write_text('Private source payload stays in the source file.')
        specs.append(SourceAuthoritySpec(str(path), ordinal, 'custom'))
    register_source_batch(project.lane('sources'), specs)
    before = hashes(project)
    request = {'node_kind': 'source_reference', 'query': 'REPORT_100%', 'node_limit': 1}
    first = call('project_evidence_map_query', request)
    assert first.status == 'ok', first.error
    cursor = first.result['source_offsets']['next_source_offset']
    last = call('project_evidence_map_query', {**request, 'source_offset': cursor})
    assert last.status == 'ok', last.error
    nodes = first.result['graph']['nodes'] + last.result['graph']['nodes']
    assert len(nodes) == 2 and len({node['key'] for node in nodes}) == 2
    assert {node['label'] for node in nodes} == {'report_100%.txt', 'report_100%-other.txt'}
    assert 'Private source payload' not in str(nodes)
    assert last.result['source_offsets']['next_source_offset'] is None
    assert hashes(project) == before


@pytest.mark.parametrize('expires_at', ['not-a-date', '2999-01-01T00:00:00+00:00'])
def test_integrity_verification_rejects_invalid_grant_lifetime_even_without_edges(federation, expires_at):
    from evidence_lane_plugin.storage import json_text
    from evidence_lane_plugin.universe_snapshot import digest
    engine, projects, _, call = federation
    records = create_and_register(federation)
    request = grant_link(federation, records)
    with engine.project_work.mutation(projects[0]) as lease, lease.transaction('universe') as connection:
        row = connection.execute('SELECT body_json FROM federation_grants WHERE grant_id=?', (request['grant_id'],)).fetchone()
        import json
        body = {**json.loads(row[0]), 'expires_at': expires_at}
        connection.execute('UPDATE federation_grants SET body_json=?,digest=? WHERE grant_id=?',
            (json_text(body), digest(body), request['grant_id']))
    assert call('project_evidence_network_verify').error.code == 'FEDERATION_INTEGRITY'


def test_integrity_verification_rejects_mini_brain_index_swapped_between_members(federation):
    engine, projects, _, call = federation
    records = create_and_register(federation)
    with engine.project_work.mutation(projects[0]) as lease, lease.transaction('universe') as connection:
        connection.execute('UPDATE federation_mini_brains SET project_id=? WHERE mini_brain_id=?',
            (projects[2].project_id, records[0][1]['lane_heads']['plan']))
    assert call('project_evidence_network_verify').error.code == 'FEDERATION_INTEGRITY'


def test_universe_inspection_checks_schema_file_identity_against_migration(federation):
    _engine, projects, _, call = federation
    project = projects[1]
    payload = b'{"different":"schema history"}'
    lane = project.lane('plan')
    lane.schema_history.write_bytes(payload)
    assert call('project_evidence_map_inspect', project=project).error.code == 'UNIVERSE_SCHEMA_INTEGRITY'
