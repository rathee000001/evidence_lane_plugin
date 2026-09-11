from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.projects import ProjectAccess, atomic_json
from evidence_lane_plugin.remote_api import (
    RemoteGateway,
    RemoteGrant,
    RemotePolicy,
    digest,
    https_origin,
    issue_remote_grant,
)
from evidence_lane_plugin.remote_server import server_config
from evidence_lane_plugin.remote_transport import RemoteClientConfig, RemoteTransport
from evidence_lane_plugin.sdk import ActionRequest, EvidenceLaneClient
from evidence_lane_plugin.writers import WriterLease
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def tls(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Isolated remote API test")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
                   .not_valid_after(datetime.now(UTC) + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .sign(key, hashes.SHA256()))
    cert, private = tmp_path / "tls.pem", tmp_path / "tls-key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    return cert, private


@contextmanager
def listening(gateway, tls):
    config = server_config(gateway, certificate=tls[0], private_key=tls[1])
    config.load()
    assert config.ssl.minimum_version >= ssl.TLSVersion.TLSv1_2
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=False)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield server
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()


@pytest.fixture
def configured(tmp_path, tls):
    source = tmp_path / "source"
    source.mkdir()
    runtime = tmp_path / "runtime"
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    token = secrets.token_urlsafe(48)
    environment = {"EL_TEST_REMOTE_TOKEN": token}
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with Engine(runtime) as engine:
        record = engine.directory.register(tmp_path / "state", source_root=source, create=True, read_only=False)
        store = engine.directory.open(record["project_id"], write=True)
        principal = str(uuid4())
        with WriterLease(store, engine.instance_id) as lease:
            access = ProjectAccess(store)
            access.initialize(writer=lease)
            access_id = access.issue(principal, ["read", "write"], [source], expires_at=datetime.fromisoformat(expires), writer=lease)
        grant = RemoteGrant(principal_id=principal, project_id=store.project_id, access_grant_id=access_id,
                            credential_env="EL_TEST_REMOTE_TOKEN", actions=["project_status", "plan_create"],
                            allow_storage_probe=True, expires_at=expires, purpose="Isolated HTTPS verification")
        policy = RemotePolicy(server_id=str(uuid4()), origin=f"https://127.0.0.1:{port}", volume_id="pytest-temporary-volume",
                              storage_class="persistent_operator_declared", grants=[grant])
        config = RemoteClientConfig(origin=policy.origin, server_id=policy.server_id, project_id=store.project_id,
                                    credential_env=grant.credential_env, ca_file=str(tls[0]),
                                    probe_file=str(tmp_path / "probe.json"))
    return runtime, policy, config, environment


def gateway_for(engine, policy, environment):
    return RemoteGateway(engine, policy, environment=environment)


def test_probe_is_owned_by_receipts_and_published_with_its_object(configured):
    runtime, policy, config, environment = configured
    with Engine(runtime) as engine:
        gateway = gateway_for(engine, policy, environment)
        result = gateway.handle('probe', json.dumps({'project_id': config.project_id,
            'nonce': secrets.token_urlsafe(48)}).encode(), policy.grants[0])
        project = engine.directory.open(config.project_id)
        receipts = project.lane('receipts')
        with project.connection(read_only=True) as root:
            assert root.execute("SELECT 1 FROM sqlite_schema WHERE name='remote_probe'").fetchone() is None
        with receipts.connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM remote_probe').fetchone()
            assert row['object_digest'] == result['object_digest']
            assert connection.execute("SELECT COUNT(*) FROM receipts WHERE kind='remote_storage_probe'").fetchone()[0] == 1
            assert receipts.read_object(row['object_digest'])


def seed(configured, tls):
    runtime, policy, config, environment = configured
    with Engine(runtime) as engine:
        gateway = gateway_for(engine, policy, environment)
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as client:
            assert client.route()["route"] is None
            with pytest.raises(LaneError, match="restart"):
                client.catalog()
            ticket = client.seed_probe()
            verification = client.verify_storage(ticket)
            assert verification["restart_observed"] is False
            assert verification["durable_verified"] is False
    return ticket


def test_https_recovery_then_project_action_and_no_global_scope(configured, tls):
    runtime, policy, config, environment = configured
    ticket = seed(configured, tls)
    with Engine(runtime) as engine:
        gateway = gateway_for(engine, policy, environment)
        with listening(gateway, tls), RemoteTransport(config, environment=environment,
                 hello=ClientHello(configured_profile="codex_vm_ephemeral")) as client:
            assert client.instance_id != ticket.previous_engine_id
            assert client.route()["route"] == "remote_api"
            assert client.route()["physical_volume_durability"] == "operator_declaration_only"
            assert [item["name"] for item in client.catalog()] == ["plan_create", "project_status"]
            result = EvidenceLaneClient(client).call("project_status", project_id=config.project_id)
            assert result.status == "ok" and result.result["object_count"] == 1, result
            assert "source_root" not in result.result
            assert environment[config.credential_env] not in json.dumps(client.connected)
            request = ActionRequest(action="plan_create", project_id=config.project_id, arguments={
                "title": "Verified remote request", "tasks": [{"task_id": "remote-one", "title": "Read source",
                    "requested_outcome": "Inspect the registered source without editing it."}]})
            created = client.send(request)
            assert created.status == "ok" and created.result["revision"] == 1, created
            wrong = request.model_copy(update={"project_id": str(uuid4())})
            with pytest.raises(LaneError) as error:
                client.send(wrong)
            assert error.value.code == "REMOTE_SCOPE_DENIED"
            with pytest.raises(LaneError):
                client.post("action", {"instance_id": client.instance_id, "policy_digest": client.policy_digest,
                                       "request": request.model_copy(update={"action": "engine_health"}).model_dump()})
            with pytest.raises(LaneError):
                client.post("action", {"instance_id": ticket.previous_engine_id, "policy_digest": client.policy_digest,
                                       "request": request.model_dump()})
            with engine.directory.open(config.project_id).lane('receipts').connection(read_only=True) as connection:
                assert connection.execute("SELECT COUNT(*) FROM receipts WHERE kind='plan_created'").fetchone()[0] == 1
            with engine.directory.open(config.project_id).lane('plan').connection(read_only=True) as connection:
                assert connection.execute('SELECT title FROM plan_revisions WHERE revision=1').fetchone()[0] == 'Verified remote request'


def test_real_tls_rejects_unknown_certificate_and_wrong_bearer(configured, tls):
    runtime, policy, config, environment = configured
    with Engine(runtime) as engine, listening(gateway_for(engine, policy, environment), tls):
        with pytest.raises(LaneError) as error:
            RemoteTransport(config.model_copy(update={"ca_file": None}), environment=environment)
        assert error.value.code == "REMOTE_TRANSPORT_FAILED"
        with pytest.raises(LaneError):
            RemoteTransport(config, environment={config.credential_env: secrets.token_urlsafe(48)})
        with pytest.raises(LaneError) as error:
            RemoteTransport(config.model_copy(update={"server_id": str(uuid4())}), environment=environment)
        assert error.value.code == "REMOTE_BINDING_CHANGED"


def test_browser_owner_routes_oversize_and_forged_probe_rejected(configured, tls):
    runtime, policy, config, environment = configured
    with (Engine(runtime) as engine,
          listening(gateway_for(engine, policy, environment), tls),
          RemoteTransport(config, environment=environment) as client):
        ticket = client.seed_probe()
        with pytest.raises(LaneError):
            client.verify_storage(ticket.model_copy(update={"nonce": secrets.token_urlsafe(48)}))
        for headers in ({"Origin": "https://attacker.invalid"}, {"Host": "attacker.invalid"}, {"Cookie": "owner=forged"}):
            response = client.http.post("/remote/v4/discover", json={}, headers=headers)
            assert response.status_code != 200
        assert client.http.post("/studio/api/control", json={"operation": "stop"}).status_code != 200
        assert client.http.post("/remote/v4/discover", content=b"x" * 1_048_577,
                                headers={"Content-Type": "application/json"}).status_code == 400
        assert client.http.post("/remote/v4/discover", json={"native_task_attestation": "verified"}).status_code == 400


def test_revocation_is_immediate_and_persisted(configured, tls):
    runtime, policy, config, environment = configured
    seed(configured, tls)
    with Engine(runtime) as engine:
        gateway = gateway_for(engine, policy, environment)
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as client:
            gateway.revoke(policy.grants[0].principal_id)
            with pytest.raises(LaneError):
                client.send(ActionRequest(action="project_status", project_id=config.project_id))
        store = engine.directory.open(config.project_id)
        with store.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute("SELECT revoked_at FROM access_grants WHERE grant_id=?",
                                      (policy.grants[0].access_grant_id,)).fetchone()[0]
    with Engine(runtime) as restarted:
        restored = gateway_for(restarted, policy, environment)
        with pytest.raises(LaneError) as error:
            restored.authenticate(environment[config.credential_env])
        assert error.value.code == "REMOTE_GRANT_EXPIRED"


def test_owner_grant_validation_and_probe_writer_exclusion(configured, tls):
    runtime, policy, config, environment = configured
    with Engine(runtime) as engine:
        with pytest.raises(LaneError):
            issue_remote_grant(engine, project_id=config.project_id, actions=["engine_health"],
                               credential_env="EL_TEST_TOKEN", purpose="forbidden global scope",
                               expires_at=datetime.now(UTC) + timedelta(hours=1))
        grant = issue_remote_grant(engine, project_id=config.project_id, actions=["project_status"],
                                   credential_env="EL_TEST_READ_TOKEN", purpose="bounded reader",
                                   expires_at=datetime.now(UTC) + timedelta(hours=1))
        assert not grant.allow_storage_probe
        store = engine.directory.open(config.project_id, write=True)
        with store.lane('receipts').connection(read_only=True) as connection:
            permissions = connection.execute("SELECT permissions_json FROM access_grants WHERE grant_id=?",
                                             (grant.access_grant_id,)).fetchone()[0]
            assert json.loads(permissions) == ["read"]
        gateway = gateway_for(engine, policy, environment)
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as client, WriterLease(store, engine.instance_id):
            with pytest.raises(LaneError):
                client.seed_probe()
            assert not Path(config.probe_file).exists()


def test_expiry_checked_on_each_remote_request(configured, tls):
    runtime, policy, config, environment = configured
    current = [datetime.now(UTC)]
    with Engine(runtime) as engine:
        gateway = RemoteGateway(engine, policy, environment=environment, clock=lambda: current[0])
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as client:
            current[0] += timedelta(hours=2)
            with pytest.raises(LaneError):
                client.post("discover", {})


def test_read_only_scope_cannot_write_or_probe(configured):
    runtime, policy, config, environment = configured
    grant = policy.grants[0].model_copy(update={"actions": ["project_status"], "allow_storage_probe": False})
    with Engine(runtime) as engine:
        gateway = gateway_for(engine, policy.model_copy(update={"grants": [grant]}), environment)
        principal = gateway.authenticate(environment[grant.credential_env])
        with pytest.raises(LaneError) as error:
            gateway.handle("probe", json.dumps({"project_id": config.project_id, "nonce": secrets.token_urlsafe(48)}).encode(), principal)
        assert error.value.code == "REMOTE_SCOPE_DENIED"
        with pytest.raises(LaneError):
            gateway.handle("action", json.dumps({"instance_id": engine.instance_id, "policy_digest": gateway.policy_digest(grant),
                           "connection_token": secrets.token_urlsafe(48),
                           "request": ActionRequest(action="plan_create", project_id=config.project_id,
                                                    arguments={"title": "denied", "tasks": []}).model_dump()}).encode(), principal)


def test_ephemeral_volume_and_expired_evidence_never_claim_durability(configured, tls):
    runtime, policy, config, environment = configured
    ephemeral = policy.model_copy(update={"storage_class": "ephemeral"})
    seed((runtime, ephemeral, config, environment), tls)
    with (Engine(runtime) as engine,
          listening(gateway_for(engine, ephemeral, environment), tls),
          RemoteTransport(config, environment=environment) as client):
        assert client._verification["restart_observed"] is True
        assert client.route()["route"] is None
    # A newly seeded probe changes the policy binding explicitly.
    Path(config.probe_file).unlink()
    seed(configured, tls)
    moment = [0.0]
    with (Engine(runtime) as engine,
          listening(gateway_for(engine, policy, environment), tls),
          RemoteTransport(config, environment=environment, monotonic=lambda: moment[0]) as client):
        assert client.route()["route"] == "remote_api"
        moment[0] = 301.0
        assert client.route()["route"] is None
        assert client.catalog()
        assert client.route()["route"] == "remote_api"


def test_recovery_requires_both_sqlite_and_cas_bytes(configured, tls):
    runtime, policy, config, environment = configured
    ticket = seed(configured, tls)
    with Engine(runtime) as engine:
        store = engine.directory.open(config.project_id)
        store.lane('receipts').object_path(ticket.object_digest).write_bytes(b"corrupted fixture")
        with listening(gateway_for(engine, policy, environment), tls), pytest.raises(LaneError):
            RemoteTransport(config, environment=environment)


def test_native_mcp_adapter_calls_verified_remote_project(configured, tls, tmp_path):
    runtime, policy, config, environment = configured
    seed(configured, tls)
    path = tmp_path / "client.json"
    atomic_json(path, config.model_dump())

    async def exercise():
        source = Path(__file__).parents[1] / "plugins/evidence-lane-plugin/src"
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "evidence_lane_plugin.mcp_adapter", "--remote-config", str(path),
                  "--host-profile", "codex_vm_ephemeral"], env={"PYTHONPATH": str(source), **environment})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session):
            await session.initialize()
            assert [item.name for item in (await session.list_tools()).tools] == ["plan_create", "project_status"]
            response = await session.call_tool("project_status", {"project_id": config.project_id, "arguments": {}})
            assert response.isError is False
            assert response.structuredContent["result"]["project_id"] == config.project_id

    with Engine(runtime) as engine, listening(gateway_for(engine, policy, environment), tls):
        asyncio.run(exercise())


def test_probe_survives_abrupt_remote_server_process_restart(configured, tls, tmp_path):
    runtime, policy, config, environment = configured
    policy = policy.model_copy(update={"grants": [policy.grants[0].model_copy(update={"actions": ["project_status"]})]})
    policy_file = tmp_path / "policy.json"
    atomic_json(policy_file, policy.model_dump())
    source = Path(__file__).parents[1] / "plugins/evidence-lane-plugin/src"

    @contextmanager
    def process():
        child = subprocess.Popen([
            sys.executable, "-m", "evidence_lane_plugin.remote_server", "--runtime-root", str(runtime),
            "--policy", str(policy_file), "--tls-certificate", str(tls[0]), "--tls-private-key", str(tls[1]),
        ], env={**os.environ, "PYTHONPATH": str(source), **environment}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                assert child.poll() is None
                try:
                    client = RemoteTransport(config, environment=environment, timeout=1)
                    client.close()
                    break
                except LaneError:
                    time.sleep(0.02)
            else:
                pytest.fail("Owned remote server did not become available")
            yield child
        finally:
            # Terminate only this test-owned child. This deliberately skips clean
            # shutdown to exercise SQLite/CAS recovery in a fresh OS process.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)

    with process(), RemoteTransport(config, environment=environment) as first:
        first_id = first.instance_id
        first.seed_probe()
    with process(), RemoteTransport(config, environment=environment) as second:
        assert second.instance_id != first_id
        assert second.route()["route"] == "remote_api"
        assert EvidenceLaneClient(second).call("project_status", project_id=config.project_id).status == "ok"


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "https://example.com/path",
                                "https://example.com?token=secret", "https://example.com#secret", "https://example.com:bad"])
def test_remote_origin_validation(url):
    with pytest.raises(LaneError):
        https_origin(url)


def test_remote_credentials_never_persisted_in_probe_or_project(configured, tls):
    runtime, _policy, config, environment = configured
    seed(configured, tls)
    secret = environment[config.credential_env].encode()
    assert secret not in Path(config.probe_file).read_bytes()
    with Engine(runtime) as engine:
        store = engine.directory.open(config.project_id)
        for lane in [store, *(store.lane(item['lane_id']) for item in store.lane_catalog())]:
            with lane.connection(read_only=True) as connection:
                dump = "\n".join(connection.iterdump()).encode()
            assert secret not in dump and digest(secret).encode() not in dump
