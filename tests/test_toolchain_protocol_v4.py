"""Actual stdio to the local engine, without installed-native claims."""
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.local_transport import LocalEndpoint
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_toolchain_catalog_and_selection_keep_attributed_host_context(tmp_path):
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    source = tmp_path / 'source'
    source.mkdir()
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine, studio_enabled=False):
        entry = engine.directory.register(tmp_path / 'project', source_root=source, create=True, read_only=False)
        project = engine.directory.open(entry['project_id'])

        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--host-profile', 'codex_cli', '--project-id', project.project_id, '--permission', 'read'],
                env={'PYTHONPATH': str(plugin / 'src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                before = {str(path): path.read_bytes() for path in project.root.rglob('*.sqlite*') if path.is_file()}
                catalog = (await session.call_tool('toolchain_catalog', {'arguments': {}})).structuredContent
                assert catalog['status'] == 'ok', catalog
                assert catalog['result']['tools']['counts']['retained'] == 103
                assert not catalog['result']['tools']['shared_bundle_ready']
                resolved = (await session.call_tool('toolchain_resolve', {'project_id': project.project_id,
                    'arguments': {'action': 'project_status', 'arguments': {}}})).structuredContent
                assert resolved['status'] == 'ok', resolved
                selection = resolved['result']['resolution']
                assert selection['selected_route'] == 'project_status.engine'
                assert selection['context']['configured_host_profile'] == 'codex_cli'
                assert selection['context']['host_profile_basis'] == 'authenticated_client_report'
                assert selection['context']['native_task_attestation'] == 'unavailable'
                spoof = (await session.call_tool('toolchain_resolve', {'project_id': project.project_id,
                    'arguments': {'action': 'project_status', 'arguments': {'host_observation': {'native_task_attestation': 'verified'}}}})).structuredContent
                assert spoof['error']['code'] == 'INVALID_ARGUMENTS'
                after = {str(path): path.read_bytes() for path in project.root.rglob('*.sqlite*') if path.is_file()}
                assert before == after

        asyncio.run(exercise())
