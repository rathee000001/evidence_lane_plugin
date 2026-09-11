"""Public stdio configuration/revocation without contacting an external provider."""
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.local_transport import LocalEndpoint
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_grant_versions_and_exact_revocation_preserve_separate_receipts(tmp_path):
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'keep.txt').write_bytes(b'untouched source')
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine, studio_enabled=False):
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project = engine.directory.open(entry['project_id'])

        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--host-profile', 'codex_cli', '--project-id', project.project_id,
                '--permission', 'read', '--permission', 'admin'], env={'PYTHONPATH': str(plugin / 'src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()

                async def call(name, arguments=None):
                    result = (await session.call_tool(name, {'project_id': project.project_id,
                        'arguments': arguments or {}})).structuredContent
                    assert result is not None
                    return result

                registration = {'plugin_id': 'protocol-grant', 'name': 'Protocol grant', 'plugin_kind': 'connector',
                    'description': 'Configuration-only fixture.', 'purpose': 'Verify versioned grant handling.',
                    'capabilities': ['bounded_read'], 'allowed_lanes': ['research'], 'allowed_actions': ['project_status'],
                    'expires_at': 'NO_EXPIRY', 'role': 'source_reader', 'role_schema': {'source_hash': 'blob_hash'},
                    'host_profiles': ['codex_cli'], 'backend_runtime': 'external_mcp',
                    'backend_id': 'fixture.not-installed', 'backend_version': '1.0.0'}
                first = await call('connector_configure', {'registration': registration})
                assert first['status'] == 'ok', first
                assert first['result']['version'] == 1 and not first['result']['backend_execution_authorized']
                changed = await call('connector_configure', {'registration': registration | {'purpose': 'Revised exact purpose.'},
                    'expected_version': 1})
                assert changed['status'] == 'ok' and changed['result']['version'] == 2
                stale = await call('connector_revoke', {'plugin_id': 'protocol-grant',
                    'expected_version': 1, 'expected_digest': first['result']['digest']})
                assert stale['error']['code'] == 'PLUGIN_VERSION_CONFLICT'
                values = {'plugin_id': 'protocol-grant', 'expected_version': 2,
                    'expected_digest': changed['result']['digest']}
                revoked = await call('connector_revoke', values)
                assert revoked['status'] == 'ok' and revoked['result']['changed']
                repeated = await call('connector_revoke', values)
                assert repeated['status'] == 'ok' and not repeated['result']['changed']
                before = project.pv_head()
                catalog = await call('connector_read')
                assert catalog['status'] == 'ok', catalog
                assert catalog['result']['registrations'][0]['active'] is False
                assert catalog['result']['registrations'][0]['version'] == 2
                adapters = catalog['result']['scopes']['adapters']
                assert adapters and all(not row['code_loaded_from_registration'] for row in adapters)
                assert all(row['backend_id'] != 'fixture.not-installed' for row in adapters)
                assert not catalog['result']['backend_execution_authorized']
                assert project.pv_head() == before

        asyncio.run(exercise())
        with project.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM extensions_versions').fetchone()[0] == 2
            assert connection.execute("SELECT count(*) FROM extensions_events WHERE event_type='revoked'").fetchone()[0] == 1
        assert (source / 'keep.txt').read_bytes() == b'untouched source'
