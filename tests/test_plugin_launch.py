from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tomllib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.launcher import ensure_local_engine, runtime_root
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.service import request_owner_control
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_reexec_preserves_mcp_stdio_until_installed_child_exits(monkeypatch):
    launcher = load_script('plugin_launch_windows_reexec_test', PLUGIN / 'scripts/run_mcp.py')
    observed = {}

    def run(command, **options):
        observed['command'] = command
        observed['options'] = options
        return SimpleNamespace(returncode=7)

    streams = {name: object() for name in ('stdin', 'stdout', 'stderr')}
    monkeypatch.setattr(launcher.os, 'name', 'nt')
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    for name, stream in streams.items():
        monkeypatch.setattr(launcher.sys, name, SimpleNamespace(buffer=stream))
    monkeypatch.setattr(
        launcher.os,
        'execve',
        lambda *_: pytest.fail('Windows re-entry must preserve the parent MCP streams.'),
    )
    result = {
        'reexec': True,
        'command': ['installed-python.exe', '-I', '-B', 'run_mcp.py'],
        'environment': {'EVIDENCE_LANE_INSTALLED_RUNTIME_ACTIVE': '1'},
    }
    with pytest.raises(SystemExit) as error:
        launcher._reexec(result)
    assert error.value.code == 7
    assert observed['command'] == result['command']
    assert observed['options'] == {
        'env': result['environment'],
        **streams,
        'close_fds': True,
        'check': False,
    }


def test_actual_plugin_manifest_matches_current_release_or_development_route(
    tmp_path, monkeypatch
):
    """Validate the shipped route without materializing the production bundle in a unit test."""
    monkeypatch.delenv('EVIDENCE_LANE_RUNTIME_ROOT', raising=False)
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(tmp_path / 'shared-studio'))
    root = runtime_root()
    config = json.loads((PLUGIN / '.mcp.json').read_text())['mcpServers']['evidence-lane']
    assert '--runtime-root' not in config['args']
    binding = json.loads(
        (PLUGIN / 'provisioning/release-binding.v4.json').read_text(encoding='utf-8')
    )
    if binding['status'] == 'RELEASE_BOUND':
        installer = load_script(
            'plugin_launch_first_detection', PLUGIN / 'scripts/first_detection.py'
        )
        validated, plan = installer.validate_binding(PLUGIN)
        assert validated == binding
        assert plan['status'] == 'COMPLETE_PREINSTALL_SOURCE_PLAN'
        assert validated['installation_enabled'] is True
        assert len(validated['assets']) == 12
        return
    with Engine(root) as engine, LocalEndpoint(engine) as endpoint:
        origin = f'http://127.0.0.1:{endpoint.server.server_port}'
        with httpx.Client(base_url=origin, headers={'Origin': origin}, trust_env=False) as studio:
            ticket = endpoint.studio.issue_ticket()
            assert studio.post('/studio/api/session', json={'ticket': ticket}).status_code == 200
            snapshot = studio.get('/studio/api/snapshot', headers={'X-Studio-Read': '1'}).json()
            assert studio.get('/studio/').status_code == 200
        async def run():
            parameters = StdioServerParameters(command=config['command'], args=config['args'],
                cwd=str(PLUGIN / config['cwd']), env={'EVIDENCE_LANE_STUDIO_ROOT': str(tmp_path / 'shared-studio')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client):
                await client.initialize()
                catalog = await client.list_tools()
                assert {tool.name for tool in catalog.tools} == {row['name'] for row in engine.registry.schemas()}
                result = await client.call_tool('engine_health', {'arguments': {}})
                assert not result.isError
                health = result.structuredContent['result']
                assert health['instance_id'] == engine.instance_id == snapshot['engine']['instance_id']
                assert health['runtime_identity'] == snapshot['engine']['runtime_identity']
                assert not health['runtime_identity']['native_installation_verified']
                context = await client.call_tool('client_context', {'arguments': {}})
                assert context.structuredContent['result']['projects'] == []
                assert engine.directory.entries() == {}
        asyncio.run(run())


def test_engine_source_mismatch_preserves_running_owner(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        engine.runtime_identity = {**engine.runtime_identity, 'source_digest': '0' * 64}
        called = []
        with pytest.raises(LaneError) as error:
            ensure_local_engine(engine.root, spawn=lambda *args, **kwargs: called.append(args))
        assert error.value.code == 'ENGINE_BUILD_MISMATCH'
        assert not called and engine.phase == 'running'


def test_plugin_launcher_starts_one_owned_service_and_reuses_it(tmp_path):
    # Keep test windows out of the user's session. The engine/endpoint/worker
    # code is real; this fixture replaces only the browser-launch callback.
    root = tmp_path / 'runtime'
    script = tmp_path / 'fixture_engine.py'
    script.write_text("""from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from evidence_lane_plugin.service import Service
if __name__ == '__main__':
    service = Service(Path(sys.argv[2]), workers=1, studio_launcher=lambda url: True)
    try:
        service.start()
        service.wait()
    finally:
        service.close()
""")
    processes, commands = [], []
    def spawn(command, **options):
        commands.append(command)
        assert command[-2:] == ['--runtime-root', str(root)]
        assert command[1:3] == ['-I', '-B']
        assert command[3] == str(PLUGIN / 'scripts/run_engine.py')
        assert options['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen([sys.executable, str(script), str(PLUGIN / 'src'), str(root)], **options)
        processes.append(process)
        return process
    try:
        first = ensure_local_engine(root, timeout=20, spawn=spawn)
        second = ensure_local_engine(root, timeout=20, spawn=spawn)
        assert first['instance_id'] == second['instance_id']
        assert len(processes) == 1 and processes[0].poll() is None
        with LocalTransport(root) as transport:
            assert transport.instance_id == first['instance_id']
    finally:
        if processes:
            request_owner_control(root, 'shutdown')
            assert processes[0].wait(timeout=15) == 0


def test_invalid_discovery_and_relative_root_do_not_launch_or_overwrite(tmp_path):
    for selected in [Path('relative'), Path('bad\nroot')]:
        with pytest.raises(LaneError) as error:
            runtime_root(selected)
        assert error.value.code == 'RUNTIME_PATH_INVALID'
    root = tmp_path / 'runtime'
    root.mkdir()
    endpoint = root / 'endpoint.json'
    endpoint.write_bytes(b'{unrelated or corrupt discovery')
    calls = []
    with pytest.raises(LaneError) as error:
        ensure_local_engine(root, spawn=lambda *args, **kwargs: calls.append(args))
    assert error.value.code == 'RUNTIME_DISCOVERY_INVALID'
    assert endpoint.read_bytes() == b'{unrelated or corrupt discovery'
    assert not calls


def test_invalid_package_arguments_are_rejected_by_runtime_parser(tmp_path, monkeypatch):
    launcher = load_script('plugin_launch_argument_test', PLUGIN / 'scripts/run_mcp.py')
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(tmp_path / 'shared-studio'))
    monkeypatch.setattr(
        launcher,
        '_first_detection',
        lambda argv: {'reexec': False, 'plugin_root': str(PLUGIN)},
    )
    with pytest.raises(SystemExit) as error:
        launcher.main(['--permission', 'write'])
    assert error.value.code == 2
    assert not (tmp_path / 'shared-studio').exists()


def test_package_and_workspace_entrypoints_and_dependencies_agree():
    workspace = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    package = tomllib.loads((PLUGIN / 'pyproject.toml').read_text())['project']
    assert package['scripts'] == workspace['scripts']
    assert package['dependencies'] == workspace['dependencies']
    assert package['scripts']['evidence-lane'] == 'evidence_lane_plugin.cli:main'
    assert package['scripts']['evidence-lane-engine'] == 'evidence_lane_plugin.service:main'
    surface = json.loads((PLUGIN / 'skills/skill-surface-registry.v4.json').read_bytes())
    names = {path.name for path in (PLUGIN / 'skills').iterdir() if path.is_dir()}
    assert names == {row['name'] for row in surface['skills']}
    assert {'open-project-session', 'manage-project-plan', 'manage-project-sources',
            'exchange-task-evidence', 'handoff-project-work'} <= names
    assert not names.intersection({'evi-start', 'evi-work', 'evi-query', 'evi-connect', 'evi-continue', 'evi-formula', 'evi-fuse'})
