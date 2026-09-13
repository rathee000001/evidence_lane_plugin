from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from evidence_lane_plugin.credentials import protect, unprotect
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import (
    MAX_ACTION_MESSAGE_BYTES,
    MAX_MESSAGE_BYTES,
    LocalEndpoint,
    LocalTransport,
)
from evidence_lane_plugin.sdk import EvidenceLaneClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Current-user Windows transport")


@pytest.mark.parametrize('path,limit', [('/v4/action', MAX_ACTION_MESSAGE_BYTES),
                                       ('/v4/disconnect', MAX_MESSAGE_BYTES)])
def test_authenticated_length_rejected_before_body_is_read(tmp_path, path, limit):
    with Engine(tmp_path) as engine, LocalEndpoint(engine) as endpoint, LocalTransport(tmp_path) as transport:
        port = endpoint.server.server_port
        headers = (f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                   f'Authorization: {transport.http.headers["Authorization"]}\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {limit + 1}\r\n\r\n')
        with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
            connection.sendall(headers.encode('ascii'))
            response = connection.recv(4096)
        assert response.startswith(b'HTTP/1.0 413'), response[:100]


def test_dpapi_round_trip_and_tamper_rejection():
    protected = protect("unit-test-credential")
    assert "unit-test-credential" not in protected
    assert unprotect(protected) == "unit-test-credential"
    with pytest.raises(LaneError):
        unprotect(protected[:-12] + "AAAAAAAAAAAA")


def test_real_loopback_request_and_secret_free_discovery(tmp_path):
    with Engine(tmp_path) as engine, LocalEndpoint(engine) as endpoint:
        with LocalTransport(tmp_path) as transport:
            assert [item["name"] for item in transport.catalog()] == [item["name"] for item in engine.registry.schemas()]
            response = EvidenceLaneClient(transport).call("engine_health")
            assert response.result["instance_id"] == engine.instance_id
            assert response.status == "ok"
        discovery = (tmp_path / "endpoint.json").read_text()
        assert endpoint.token not in discovery
    assert not (tmp_path / "endpoint.json").exists()


@pytest.mark.parametrize("headers,status", [
    ({}, 401),
    ({"Authorization": "Bearer wrong"}, 401),
    ({"Origin": "https://attacker.invalid"}, 403),
    ({"Host": "attacker.invalid"}, 403),
])
def test_unauthenticated_browser_and_wrong_host_rejected(tmp_path, headers, status):
    with (Engine(tmp_path) as engine, LocalEndpoint(engine) as endpoint,
          httpx.Client(trust_env=False) as client):
        result = client.post(f"http://127.0.0.1:{endpoint.server.server_port}/v4/action",
                             headers=headers, json={"action": "engine_health"})
        assert result.status_code == status


def test_draining_engine_rejects_new_work(tmp_path):
    with Engine(tmp_path) as engine, LocalEndpoint(engine), LocalTransport(tmp_path) as transport:
        engine.begin_drain()
        result = EvidenceLaneClient(transport).call("engine_health")
        assert result.status == "ok" and result.result["phase"] == "draining"
        result = EvidenceLaneClient(transport).call("project_status")
        assert result.error.code == "ENGINE_DRAINING"


@pytest.mark.parametrize('entrypoint', ['module', 'outer_binding'])
def test_native_mcp_stdio_subprocess_lists_and_calls_engine(tmp_path, entrypoint):
    source = Path(__file__).parents[1] / "plugins/evidence-lane-plugin/src"

    async def exercise():
        args = ["-m", "evidence_lane_plugin.mcp_adapter", "--runtime-root", str(tmp_path)]
        if entrypoint == 'outer_binding':
            args = ['-c', ('import runpy,sys; from pathlib import Path; '
                    'runpy.run_path(sys.argv[1])["run_server"](runtime_root=Path(sys.argv[2]))'),
                    str(source.parent / 'mcp/evidence_lane_mcp.py'), str(tmp_path)]
        parameters = StdioServerParameters(
            command=sys.executable,
            args=args,
            env={"PYTHONPATH": str(source)},
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.version == "4.0.4"
            catalog = await session.list_tools()
            assert [tool.name for tool in catalog.tools] == [item["name"] for item in engine.registry.schemas()]
            observation = engine.clients.status()[0]["host_observation"]
            assert observation["client"]["protocol"] == "mcp_stdio"
            assert observation["client"]["protocol_version"] == initialized.protocolVersion
            assert observation["client"]["peer_name"]
            assert observation["native_task_attestation"] == "unavailable"
            result = await session.call_tool("engine_health", {"arguments": {}})
            assert not result.isError
            assert result.structuredContent["result"]["instance_id"] == engine.instance_id
            invalid = await session.call_tool("engine_health", {"arguments": {"forged": True}})
            assert invalid.isError
            assert "unit-test-credential" not in json.dumps(result.model_dump())

    with Engine(tmp_path) as engine, LocalEndpoint(engine):
        asyncio.run(exercise())
