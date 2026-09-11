from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.cli import _parser
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.mcp_adapter import configured_selections, tool_from_action
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'


@asynccontextmanager
async def native(runtime, project, *, permissions=('read',), entrypoint='adapter'):
    commands = {
        'adapter':['-m','evidence_lane_plugin.mcp_adapter'],
        'cli':['-m','evidence_lane_plugin.cli','serve'],
        # Protocol tests target the adapter that the installed package launcher
        # reaches after first detection. Release materialization has its own
        # exact-asset and installed-self-test qualification.
        'package':['-B','-m','evidence_lane_plugin.mcp_adapter'],
    }
    arguments = commands[entrypoint] + ['--runtime-root', str(runtime), '--project-id',project,
                                      '--host-profile','codex_desktop']
    for permission in permissions:
        arguments += ['--permission',permission]
    parameters = StdioServerParameters(command=sys.executable, args=arguments, env={'PYTHONPATH':str(PLUGIN / 'src')})
    async with (stdio_client(parameters) as (read,write),
                ClientSession(read,write,read_timeout_seconds=timedelta(seconds=20)) as session):
        await session.initialize()
        yield session


async def call(session, action, project=None, **arguments):
    result = await session.call_tool(action, {'project_id':project,'arguments':arguments})
    assert result.structuredContent is not None, result
    return result.structuredContent


def projects(engine, tmp_path):
    entries = []
    for name in ['selected','unselected']:
        source = tmp_path / name / 'source'
        source.mkdir(parents=True)
        (source / 'input.txt').write_text('Fixture source\n')
        entries.append(engine.directory.register(tmp_path / name / 'state', source_root=source, create=True, read_only=False))
    return [engine.directory.open(entry['project_id'], write=True) for entry in entries]


@pytest.mark.parametrize('entrypoint',['adapter','cli'])
def test_explicit_native_selection_is_readonly_and_does_not_select_another_project(tmp_path, entrypoint):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        selected, other = projects(engine, tmp_path)
        before = selected.database.read_bytes()
        async def run():
            async with native(engine.root, selected.project_id, entrypoint=entrypoint) as session:
                info = (await call(session,'client_context'))['result']
                assert info['engine_instance_id'] == engine.instance_id
                assert info['native_task_attestation'] == 'not_provided'
                assert info['projects'] == [{'project_id':selected.project_id, 'permissions':['read'],
                    'source_root':str(selected.source_root),'state_root':str(selected.root),'authorization':'current'}]
                assert info['host_observation']['client']['configured_profile'] == 'codex_desktop'
                assert (await call(session,'project_status',selected.project_id))['status'] == 'ok'
                assert (await call(session,'project_status',other.project_id))['error']['code'] == 'PROJECT_NOT_SELECTED'
                denied = await call(session,'plan_create',selected.project_id,title='Denied',
                    tasks=[{'task_id':'one','title':'One','requested_outcome':'A valid task requiring write permission.'}])
                assert denied['error']['code'] == 'PROJECT_NOT_SELECTED'
                for action in ['connector_read','accelerator_read']:
                    assert (await call(session,action,selected.project_id))['status'] == 'ok'
                assert selected.database.read_bytes() == before
        asyncio.run(run())


def test_mjs_bridge_reaches_first_detection_before_mcp_protocol() -> None:
    manifest = json.loads((PLUGIN / '.mcp.json').read_bytes())['mcpServers']['evidence-lane']
    binding = json.loads(
        (PLUGIN / 'provisioning/release-binding.v4.json').read_text(encoding='utf-8')
    )
    assert manifest['command'] == 'node'
    assert manifest['args'][:3] == ['./mcp/server.mjs', '--transport', 'stdio']
    bridge = (PLUGIN / 'mcp/server.mjs').read_text(encoding='utf-8')
    assert 'scripts", "run_mcp.py"' in bridge
    assert binding['status'] in {'SOURCE_TEMPLATE_UNBOUND', 'RELEASE_BOUND'}
    if binding['status'] == 'RELEASE_BOUND':
        assert binding['installation_enabled'] is True
        assert len(binding['assets']) == 12
    else:
        assert binding['installation_enabled'] is False
        assert binding['assets'] == []


def registration():
    return {'plugin_id':'fixture-reader','name':'Fixture reader','plugin_kind':'connector',
        'description':'Read a selected fixture.','purpose':'Test an explicit metadata grant.',
        'config_env_keys':['FIXTURE_API_TOKEN'],'capabilities':['fixture_read'], 'allowed_lanes':['sources'],
        'allowed_actions':['project_status'],'write_roots':[],'expires_at':'NO_EXPIRY',
        'role':'fixture_reader','role_schema':{'source_hash':'blob_hash'},'host_profiles':['codex_desktop'],
        'backend_runtime':'external_mcp'}


def test_native_admin_configuration_and_exact_revocation_use_existing_owner_services(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        selected, _ = projects(engine,tmp_path)
        async def run():
            async with native(engine.root,selected.project_id,permissions=('read','write','admin')) as session:
                empty = (await call(session,'connector_read',selected.project_id))['result']
                assert empty['registrations'] == [] and not empty['backend_execution_authorized']
                first = await call(session,'connector_configure',selected.project_id,registration=registration())
                assert first['status'] == 'ok', first
                body = first['result']
                assert body['version'] == 1 and not body['backend_execution_authorized']
                assert (await call(session,'connector_configure',selected.project_id,registration=registration()))['error']['code'] == 'PLUGIN_VERSION_CONFLICT'
                same = await call(session,'connector_configure',selected.project_id,registration=registration(),expected_version=1)
                assert not same['result']['changed']
                page = (await call(session,'connector_read',selected.project_id))['result']
                assert page['registrations'][0]['config_env_keys'] == ['FIXTURE_API_TOKEN']
                assert page['registrations'][0]['active']
                bad = await call(session,'connector_revoke',selected.project_id,plugin_id='fixture-reader',expected_version=2,expected_digest=body['digest'])
                assert bad['error']['code'] == 'PLUGIN_VERSION_CONFLICT'
                revoked = await call(session,'connector_revoke',selected.project_id,plugin_id='fixture-reader',expected_version=1,expected_digest=body['digest'])
                assert revoked['result']['revoked'] and revoked['result']['changed']
                repeated = await call(session,'connector_revoke',selected.project_id,plugin_id='fixture-reader',expected_version=1,expected_digest=body['digest'])
                assert repeated['result']['revoked'] and not repeated['result']['changed']
                assert not (await call(session,'connector_read',selected.project_id))['result']['registrations'][0]['active']
                settings = (await call(session,'accelerator_read',selected.project_id))['result']
                assert settings['revision'] == 0 and settings['requested_profile'] == 'cpu'
                config = {'requested_profile':'cpu','purpose':'Use CPU for this fixture.','action_classes':['RETRIEVAL'],'expires_at':'NO_EXPIRY'}
                changed = await call(session,'accelerator_configure',selected.project_id,config=config,expected_revision=0)
                assert changed['status'] == 'ok', changed
                assert changed['result']['revision'] == 1 and not changed['result']['backend_selection_verified']
                readback = (await call(session,'accelerator_read',selected.project_id))['result']
                assert readback['revision'] == 1 and readback['config']['purpose'] == config['purpose']
                assert (await call(session,'accelerator_configure',selected.project_id,config=config,expected_revision=0))['error']['code'] == 'ACCELERATOR_REVISION_CONFLICT'
        asyncio.run(run())
        with selected.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM extensions_events').fetchone()[0] == 2
            assert connection.execute('SELECT count(*) FROM accelerator_settings').fetchone()[0] == 1
            assert connection.execute('SELECT count(*) FROM access_grants WHERE revoked_at IS NULL').fetchone()[0] == 0


def test_native_read_detects_registration_tampering(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        selected, _ = projects(engine,tmp_path)
        async def run():
            async with native(engine.root, selected.project_id,permissions=('read','admin')) as session:
                assert (await call(session,'connector_configure',selected.project_id,registration=registration()))['status'] == 'ok'
                with engine.project_work.mutation(selected) as lease, lease.transaction('receipts') as connection:
                    row = connection.execute('SELECT registration_json FROM extensions_versions').fetchone()
                    value = json.loads(row[0])
                    value['purpose'] = 'Unrecorded alteration'
                    connection.execute('UPDATE extensions_versions SET registration_json=?',(json.dumps(value),))
                assert (await call(session,'connector_read',selected.project_id))['error']['code'] == 'PLUGIN_RECORD_INTEGRITY'
        asyncio.run(run())


@pytest.mark.parametrize('extra', [
    ['--permission','write'],
    ['--project-id','not-a-uuid'],
    ['--project-id','12345678-1234-1234-1234-123456789abc','--project-id','12345678-1234-1234-1234-123456789abc'],
])
def test_invalid_local_selection_configuration_cannot_become_a_grant(extra):
    parser = _parser()
    args = parser.parse_args(['serve','--runtime-root','fixture'] + extra)
    with pytest.raises(SystemExit):
        configured_selections(args,parser)


def test_local_selection_cannot_override_remote_oauth():
    parser = _parser()
    args = parser.parse_args(['serve','--remote-config','fixture','--project-id','12345678-1234-1234-1234-123456789abc'])
    with pytest.raises(SystemExit):
        configured_selections(args,parser)


def test_all_native_schema_references_resolve_without_mutating_registry_contracts(tmp_path):
    engine = Engine(tmp_path)
    original = engine.registry.schemas()
    def inspect(value, root):
        if isinstance(value, list):
            for item in value:
                inspect(item, root)
        elif isinstance(value, dict):
            if '$ref' in value:
                reference = value['$ref']
                assert reference.startswith('#/')
                target = root
                for key in reference[2:].split('/'):
                    target = target[key.replace('~1','/').replace('~0','~')]
                assert isinstance(target, dict)
            for item in value.values():
                inspect(item, root)
    for action in original:
        schema = tool_from_action(action).inputSchema
        inspect(schema, schema)
    assert engine.registry.schemas() == original
