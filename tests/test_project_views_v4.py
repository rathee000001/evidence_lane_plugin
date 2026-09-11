from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.mcp_adapter import tool_from_action
from evidence_lane_plugin.mcp_apps import (
    GOVERNED_PANEL_URI,
    MCP_APP_MIME_TYPE,
    PanelRead,
    PanelSnapshot,
    governed_panel_html,
)
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.registry import ActionRegistry, ActionSpec
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.writers import WriterLease
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from tests.storage_fixtures_v4 import declare_local_storage


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    runtime = tmp_path_factory.mktemp('panel-engine')
    declare_local_storage(runtime, tmp_path_factory.getbasetemp())
    with Engine(runtime) as value:
        yield value


@pytest.fixture
def project(engine, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'unchanged.txt').write_bytes(b'Selected source must stay unchanged.')
    record = engine.directory.register(tmp_path / 'project', source_root=source, create=True,
        read_only=False, display_name='Panel fixture', capture_route='ENV_BUILDER_SPARSE')
    store = engine.directory.open(record['project_id'], write=True)
    _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
        project_id=store.project_id, permissions=['read', 'write'])]))
    yield store, client
    assert (source / 'unchanged.txt').read_bytes() == b'Selected source must stay unchanged.'


def call(engine, client, action, project_id=None, **arguments):
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action,
        project_id=project_id, arguments=arguments), client)


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def make_plan(engine, store):
    with WriterLease(store, engine.instance_id) as writer:
        PlanStore(store).create(PlanCreate(title='Current Plan', tasks=[TaskDefinition(
            task_id='task-' + str(i), title='Inspect source ' + str(i),
            requested_outcome='Inspect the selected source without modifying it.') for i in range(1, 5)]),
            writer, actor_id='test-fixture')


def test_only_registry_declared_views_have_read_only_mcp_metadata(engine):
    panels = [row for row in engine.registry.schemas() if 'ui' in row]
    assert {row['name'] for row in panels} == {'render_project_panel', 'render_runtime_panel'}
    for row in panels:
        tool = tool_from_action(row)
        assert tool.meta == {'ui': {'resourceUri': GOVERNED_PANEL_URI, 'visibility': ['model']}}
        assert tool.annotations.readOnlyHint and not tool.annotations.destructiveHint
    ordinary = next(row for row in engine.registry.schemas() if row['name'] == 'plan_create')
    assert tool_from_action(ordinary).meta is None


@pytest.mark.parametrize('change', [
    {'ui_resource': 'file:///private'}, {'mutates': True, 'permission': 'write'},
    {'queued': True}, {'requires_delta': True}, {'permission': 'project_admin'},
])
def test_registry_cannot_attach_an_app_to_mutation_or_arbitrary_resource(change):
    spec = ActionSpec('test_panel', 'Read a fixture', PanelRead, PanelSnapshot,
        lambda context, request: None, ui_resource=GOVERNED_PANEL_URI)
    with pytest.raises(LaneError):
        ActionRegistry().register(replace(spec, **change))


def test_runtime_panel_reports_observations_without_selecting_or_leaking_other_projects(engine, project):
    store, client = project
    before = hashes(store.root)
    result = call(engine, client, 'render_runtime_panel')
    assert result.status == 'ok', result
    panel = result.result
    assert panel['project_id'] is None and panel['root_pv'] is None and panel['plan'] is None
    assert store.project_id not in json.dumps(panel)
    assert panel['status'] == 'observed' and panel['native_task_attestation'] == 'not_provided'
    assert all(row['state'] == 'registered' and row['revision'] is None for row in panel['lanes'])
    assert len({row['id'] for row in panel['lanes']}) == len(panel['lanes'])
    assert hashes(store.root) == before


def test_empty_project_panel_does_not_initialize_plan_or_session(engine, project):
    store, client = project
    before = hashes(store.root)
    expected_head = store.pv_head()
    expected_lanes = {r['lane_id']: r['head_digest'] for r in store.lane_catalog()}
    result = call(engine, client, 'render_project_panel', store.project_id)
    assert result.status == 'ok', result
    panel = result.result
    assert panel['plan']['state'] == 'no_plan' and panel['session']['state'] == 'none'
    assert panel['root_pv'] == expected_head
    assert {r['id']: r['head_digest'] for r in panel['lanes']} == expected_lanes
    assert panel['title'] == 'Panel fixture' and panel['read_only'] and not panel['mutation_performed']
    assert hashes(store.root) == before


def test_project_panel_pages_exact_current_plan_and_preserves_all_bytes(engine, project):
    store, client = project
    make_plan(engine, store)
    before = hashes(store.root)
    first = call(engine, client, 'render_project_panel', store.project_id, limit=2)
    last = call(engine, client, 'render_project_panel', store.project_id, offset=2, limit=2)
    assert first.status == last.status == 'ok', (first, last)
    assert first.result['plan']['truncated'] and not last.result['plan']['truncated']
    assert [r['task_id'] for r in first.result['plan']['tasks']] == ['task-1', 'task-2']
    assert [r['task_id'] for r in last.result['plan']['tasks']] == ['task-3', 'task-4']
    assert last.result['plan']['tasks'][0]['dependencies'] == ['task-2']
    assert first.result['root_pv'] == last.result['root_pv'] == store.pv_head()
    assert all(row['head_digest'] for row in first.result['lanes'] if row['state'] == 'published')
    assert hashes(store.root) == before


def test_project_panel_requires_selected_project_even_for_project_administrator(engine, project):
    store, _ = project
    _, outsider = engine.clients.connect(ConnectRequest(manage_projects=True))
    before = hashes(store.root)
    denied = call(engine, outsider, 'render_project_panel', store.project_id)
    assert denied.status == 'error' and denied.error.code == 'PROJECT_NOT_SELECTED'
    assert hashes(store.root) == before


def test_project_panel_cannot_accept_mutation_arguments(engine, project):
    store, client = project
    before = hashes(store.root)
    for arguments in [{'limit': 101}, {'offset': -1}, {'create': True}, {'confirmation': 'APPROVE'}]:
        assert call(engine, client, 'render_project_panel', store.project_id, **arguments).status == 'error'
    assert hashes(store.root) == before


def test_project_panel_fail_closed_on_root_reference_corruption(engine, project):
    store, client = project
    make_plan(engine, store)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE root_pv_head SET head_digest=?", ('f' * 64,))
    before = hashes(store.root)
    result = call(engine, client, 'render_project_panel', store.project_id)
    assert result.status == 'error' and result.error.code == 'UNIVERSE_ROOT_INTEGRITY'
    assert hashes(store.root) == before


def test_project_panel_fail_closed_on_lane_bytes_corruption(engine, project):
    store, client = project
    make_plan(engine, store)
    with sqlite3.connect(store.lane('plan').database) as connection:
        connection.execute("UPDATE plan_revisions SET title='unexpected change'")
    before = hashes(store.root)
    result = call(engine, client, 'render_project_panel', store.project_id)
    assert result.status == 'error' and result.error.code == 'LANE_HEAD_MISMATCH'
    assert hashes(store.root) == before


def test_project_panel_reads_live_session_without_transition(engine, project):
    store, client = project
    boot = call(engine, client, 'session_boot', store.project_id,
        reported_session_id='protocol-only-fixture', expected_root_pv_digest=store.pv_head()['head_digest'])
    assert boot.status == 'ok', boot
    before = hashes(store.root)
    result = call(engine, client, 'render_project_panel', store.project_id)
    assert result.status == 'ok', result
    assert result.result['session']['state'] == 'active'
    assert result.result['session']['capture_bound'] and result.result['session']['owner_authenticated']
    assert result.result['session']['native_task_attestation'] == 'not_provided'
    assert not result.result['native_goal_completed'] and hashes(store.root) == before


def test_actual_stdio_resource_discovery_read_and_structured_fallback(engine, project):
    store, _ = project
    make_plan(engine, store)
    before = hashes(store.root)
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--project-id', store.project_id], env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] /
                    'plugins/evidence-lane-plugin/src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                catalog = await session.list_tools()
                tool = next(t for t in catalog.tools if t.name == 'render_project_panel')
                assert tool.meta['ui']['resourceUri'] == GOVERNED_PANEL_URI
                resources = await session.list_resources()
                assert len(resources.resources) == 1 and str(resources.resources[0].uri) == GOVERNED_PANEL_URI
                resource = (await session.read_resource(GOVERNED_PANEL_URI)).contents[0]
                assert resource.mimeType == MCP_APP_MIME_TYPE
                assert resource.meta['ui']['csp'] == {'connectDomains': [], 'resourceDomains': []}
                assert store.project_id not in resource.text and 'data:image/png;base64,' in resource.text
                response = await session.call_tool('render_project_panel', {'project_id': store.project_id,
                    'arguments': {'offset': 1, 'limit': 2}})
                assert not response.isError, response
                structured = response.structuredContent
                assert structured['status'] == 'ok' and structured['result']['project_id'] == store.project_id
                assert len(structured['result']['plan']['tasks']) == 2
                assert json.loads(response.content[0].text) == structured
                with pytest.raises(McpError, match='Unknown Evidence Lane UI resource'):
                    await session.read_resource('ui://evidence-lane/unknown.html')
        asyncio.run(exercise())
    assert hashes(store.root) == before


def test_static_html_has_only_navigation_and_no_external_dependencies():
    document = governed_panel_html()
    assert 'data-tab="plan"' in document and 'data-tab="lanes"' in document
    for forbidden in ('HIL', 'accepted_pv', 'tools/call', 'sendFollowUpMessage', '<form',
                      'innerHTML', 'fetch(', 'XMLHttpRequest', '<script src=', 'http://'):
        assert forbidden not in document
    assert 'event.source !== window.parent' in document and 'message.result?.protocolVersion' in document


def test_panel_is_a_read_only_gateway_projection_without_human_write_access(engine, project):
    store, _ = project
    make_plan(engine, store)
    gateway = StudioGateway(engine)
    _, session = gateway.exchange(gateway.issue_ticket())
    before = hashes(store.root)
    result = gateway.command('read', {'project_id': store.project_id, 'action': 'render_project_panel',
        'arguments': {'offset': 1, 'limit': 1}}, session)
    assert result['project_id'] == store.project_id and result['plan']['tasks'][0]['task_id'] == 'task-2'
    for route in ('project', 'backup', 'view-export', 'accelerator', 'plugin', 'git-restore'):
        with pytest.raises(LaneError) as error:
            gateway.command(route, {'project_id': store.project_id}, session)
        assert error.value.code == 'STUDIO_READ_ONLY'
    assert hashes(store.root) == before
