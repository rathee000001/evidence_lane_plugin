from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.first_class_workflows import (
    BrainScalingRequest,
    BrainSlice,
    run_brain_scaling,
)
from evidence_lane_plugin.host_routing import ClientHello, HostDetector, select_host_route
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.sdk import ActionRequest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.storage_fixtures_v4 import declare_local_storage

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'


def test_native_owner_can_register_select_boot_and_close_while_normal_client_cannot(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_text('Source stays unchanged.')
    state = tmp_path / 'project-state'
    declare_local_storage(tmp_path / 'engine', state)
    with Engine(tmp_path / 'engine') as engine, LocalEndpoint(engine, studio_enabled=False) as endpoint:
        async def exercise(manage):
            args = ['-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root)]
            if manage:
                definition = json.loads((PLUGIN / '.mcp.json').read_bytes())['mcpServers']['evidence-lane']
                assert '--local-project-administration' in definition['args']
                args.append('--manage-projects')
            parameters = StdioServerParameters(command=sys.executable, args=args,
                                               env={'PYTHONPATH': str(PLUGIN / 'src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                async def call(name, arguments=None, project_id=None):
                    response = await session.call_tool(name, {'arguments': arguments or {}, 'project_id': project_id})
                    assert response.structuredContent is not None, response
                    return response.structuredContent
                result = await call('project_register', {'state_root': str(state), 'source_root': str(source), 'create': True, 'read_only': False,
                    'display_name': 'Protocol project', 'sensitivity': 'CONFIDENTIAL', 'capture_route': 'ENV_BUILDER_SPARSE'})
                if not manage:
                    assert result['error']['code'] == 'PERMISSION_DENIED'
                    assert not state.exists()
                    return
                assert result['status'] == 'ok', result
                project = result['result']['project_id']
                assert result['result']['display_name'] == 'Protocol project'
                assert result['result']['registration_bound'] and result['result']['sensitivity_enforcement'] == 'metadata_label_only'
                assert (await call('project_select', {'project_id': project, 'permissions': ['read', 'write']}))['status'] == 'ok'
                doctor = await call('runtime_doctor')
                assert doctor['status'] == 'ok' and doctor['result']['flash']['state'] == 'verified'
                storage = (await call('storage_status', project_id=project))['result']
                assert storage['registration']['capture_route'] == 'ENV_BUILDER_SPARSE'
                opened = await call('session_boot', {'reported_session_id': 'native-protocol-fixture',
                    'expected_root_pv_digest': storage['root_pv']['head_digest']}, project)
                assert opened['status'] == 'ok', opened
                active = (await call('session_status', project_id=project))['result']
                assert active['capture_bound'] and active['state'] == 'active'
                assert (await call('project_deselect', {'project_id': project, 'expected_permissions': ['read', 'write']}))['error']['code'] == 'SESSION_ACTIVE'
                assert (await call('session_exit', {'session_id': active['session_id'], 'expected_generation': active['generation'],
                    'expected_event_digest': active['event_digest'], 'reason': 'Protocol fixture complete.'}, project))['status'] == 'ok'
                assert (await call('project_deselect', {'project_id': project, 'expected_permissions': ['read', 'write']}))['status'] == 'ok'
                assert (await call('project_status', project_id=project))['error']['code'] == 'PROJECT_NOT_SELECTED'
        async def exercise_both():
            await exercise(False)
            deadline = asyncio.get_running_loop().time() + 2
            while engine.clients.status() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert not engine.clients.status()
            await exercise(True)

        asyncio.run(exercise_both())
        with httpx.Client(trust_env=False) as http:
            assert http.get(f'http://127.0.0.1:{endpoint.server.server_port}/studio/').status_code == 404
        assert (source / 'input.txt').read_text() == 'Source stays unchanged.'


def test_instruction_discovery_keeps_override_and_recall_separate_without_loading_siblings(tmp_path):
    source = tmp_path / 'source'
    nested = source / 'nested'
    nested.mkdir(parents=True)
    (source / 'AGENTS.md').write_text('Root guidance')
    (source / 'MEMORY.md').write_text('Workspace recall')
    (nested / 'AGENTS.md').write_text('Not selected because an override exists')
    (nested / 'AGENTS.override.md').write_text('Scoped override')
    (tmp_path / 'AGENTS.md').write_text('Outside the selected source root')
    with Engine(tmp_path / 'engine') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project = engine.directory.open(entry['project_id'])
        _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id)]))
        dispatch = PublicActionSDKDispatcher(engine)
        request = ActionRequest(action='instructions_inspect', project_id=project.project_id, arguments={'cwd_relative': 'nested'})
        before = project.pv_head()
        result = dispatch.execute(request, client)
        assert result.status == 'ok', result
        assert [row['locator'] for row in result.result['instructions']] == ['AGENTS.md', 'nested/AGENTS.override.md']
        assert [row['locator'] for row in result.result['workspace_recall']] == ['MEMORY.md']
        assert not result.result['host_recall'] and not result.result['native_loaded_chain_attested']
        assert project.pv_head() == before
        denied = dispatch.execute(request.model_copy(update={'arguments': {'include_host_recall': True}}), client)
        assert denied.error.code == 'HOST_RECALL_OWNER_SCOPE_REQUIRED'


def test_recipe_uses_registered_sources_and_preserves_plan_and_source_heads(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.py').write_text('answer = 42\n')
    with Engine(tmp_path / 'engine') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project = engine.directory.open(entry['project_id'], write=True)
        _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id, permissions=['read', 'write'])]))
        dispatch = PublicActionSDKDispatcher(engine)
        registered = dispatch.execute(ActionRequest(action='source_register', project_id=project.project_id,
            arguments={'sources': [str(source / 'app.py')]}), client)
        assert registered.status == 'ok', registered
        batch = registered.result['result']['source_authority']['batch_id']
        before = project.pv_head()
        result = dispatch.execute(ActionRequest(action='project_recipe', project_id=project.project_id,
            arguments={'batch_id': batch, 'requested_outcome': 'Maintain this Python source.'}), client)
        assert result.status == 'ok', result
        assert result.result['project_type'] == 'CODE'
        assert result.result['selected_sector_lanes'] == ['local_code']
        assert 'chat_lineage' in result.result['authority_references']
        assert result.result['proposal_only'] and not result.result['plan_changed']
        custom = dispatch.execute(ActionRequest(action='project_recipe', project_id=project.project_id,
            arguments={'batch_id': batch, 'requested_outcome': 'Maintain this source.',
                       'explicit_project_type': 'CUSTOM:Laboratory archive'}), client)
        assert custom.status == 'ok', custom
        assert custom.result['project_type'] == 'CUSTOM:Laboratory archive'
        assert custom.result['selected_sector_lanes'] == ['local_code']
        assert project.pv_head() == before


def test_original_deterministic_slice_selection_preserves_estimation_boundary():
    request = BrainScalingRequest(authority_id='canon', slices=[
        BrainSlice(content_id='b', content_sha256='b' * 64, token_count=8, priority=1),
        BrainSlice(content_id='a', content_sha256='a' * 64, token_count=5, priority=2),
        BrainSlice(content_id='c', content_sha256='c' * 64, token_count=6)], token_budget=12, max_slices=2)
    result = run_brain_scaling(request)
    assert [row.content_id for row in result.selected_slices] == ['a', 'c']
    assert result.used_tokens == 11 and result == run_brain_scaling(request)
    assert result.token_count_basis == 'caller_supplied_estimates'
    assert not result.content_identity_reverified


@pytest.mark.parametrize('system', ['Darwin', 'Linux'])
def test_reduced_host_route_never_claims_windows_studio_or_managed_bundle(system):
    observation = HostDetector(system=lambda: system, which=lambda _: None).inspect(trigger='client_connect',
        client=ClientHello(configured_profile='codex_cli'))
    route = select_host_route(observation)
    assert route['route'] == 'local_loopback' and not route['studio_supported']
    assert not route['managed_windows_toolchain'] and not observation.engine_studio_platform_supported
    assert observation.native_task_attestation == 'unavailable'


@pytest.mark.parametrize('profile', ['codex_vm_persistent', 'codex_vm_ephemeral'])
def test_explicit_vm_profile_cannot_create_a_local_owner_session(tmp_path, profile):
    with Engine(tmp_path / 'runtime') as engine:
        with pytest.raises(LaneError) as error:
            engine.clients.connect(ConnectRequest(hello=ClientHello(configured_profile=profile), manage_projects=True))
        assert error.value.code == 'REMOTE_DURABILITY_UNVERIFIED'
        assert not engine.clients.status() and not engine.directory.entries()
