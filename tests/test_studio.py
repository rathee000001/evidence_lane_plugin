from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool


@pytest.fixture
def studio(tmp_path):
    engine = Engine(tmp_path / "runtime", capabilities=CapabilityMonitor(device_probe=list),
        worker_pool=WorkerPool((WorkerOperation('render_lane_view','evidence_lane_plugin.artifact_contract',
            'render_lane_view_worker',dependencies=('langgraph','langchain_core','graphviz')),),workers=1))
    engine.start()
    endpoint = LocalEndpoint(engine)
    endpoint.start()
    origin = f"http://127.0.0.1:{endpoint.server.server_port}"
    with httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False, timeout=5) as client:
        yield engine, endpoint, client
    endpoint.close()
    engine.stop()


def login(endpoint, client):
    ticket = endpoint.studio.issue_ticket()
    response = client.post("/studio/api/session", json={"ticket": ticket})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/studio/" in cookie
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return ticket


def add_project(engine, tmp_path, name):
    source = tmp_path / (name + "-source")
    source.mkdir()
    return engine.directory.register(tmp_path / (name + "-state"), source_root=source, create=True, read_only=False)


def test_studio_ticket_single_use_expiry_and_engine_binding(tmp_path):
    current = [datetime.now(UTC)]
    engine = Engine(tmp_path / "runtime")
    gateway = StudioGateway(engine, clock=lambda: current[0])
    ticket = gateway.issue_ticket()
    token, session = gateway.exchange(ticket)
    with pytest.raises(LaneError, match="Open Studio"):
        gateway.exchange(ticket)
    assert gateway.authenticate(gateway.cookie(token), csrf=session.csrf) == session
    with pytest.raises(LaneError):
        gateway.authenticate(gateway.cookie(token), csrf="wrong")
    other = StudioGateway(Engine(tmp_path / "other"))
    with pytest.raises(LaneError):
        other.authenticate(gateway.cookie(token))
    ticket = gateway.issue_ticket()
    current[0] += timedelta(seconds=61)
    with pytest.raises(LaneError):
        gateway.exchange(ticket)
    current[0] += timedelta(hours=12)
    with pytest.raises(LaneError):
        gateway.authenticate(gateway.cookie(token))


def test_anonymous_static_only_strict_origin_no_native_token_upgrade(studio):
    engine, endpoint, client = studio
    static = client.get("/studio/")
    assert static.status_code == 200
    assert "frame-ancestors 'none'" in static.headers["content-security-policy"]
    assert "unsafe-inline" not in static.headers["content-security-policy"]
    assert endpoint.token not in static.text
    assert engine.instance_id not in static.text
    assert client.get("/studio/app.js").status_code == 200
    assert client.get("/studio/../endpoint.json").status_code == 404
    assert client.get("/studio/api/snapshot").status_code == 403
    assert client.get("/studio/api/snapshot", headers={"X-Studio-Read": "1"}).status_code == 401
    ticket = endpoint.studio.issue_ticket()
    for origin in ("https://foreign.invalid", "null", "http://localhost:1"):
        assert client.post("/studio/api/session", json={"ticket": ticket}, headers={"Origin": origin}).status_code == 403
    with LocalTransport(engine.root) as native:
        credentials = native.http.headers["authorization"]
        assert client.post("/studio/api/session", json={"ticket": ticket}, headers={"Authorization": credentials}).status_code == 403
        assert native.http.post("/studio/api/session", json={"ticket": ticket}).status_code == 403
    # Rejected cross-origin and native requests did not consume the ticket.
    assert client.post("/studio/api/session", json={"ticket": ticket}).status_code == 200
    assert client.post("/v4/catalog", json={}).status_code == 403


def test_studio_project_route_absent_and_snapshot_read_only(studio, tmp_path):
    engine, endpoint, client = studio
    project = add_project(engine, tmp_path, "selected")
    login(endpoint, client)
    before_entries = engine.directory.entries()
    response = client.post("/studio/api/project", json={"create": True})
    assert response.status_code == 404 and response.json()["error"] == "UNKNOWN_ROUTE"
    assert engine.directory.entries() == before_entries
    store = engine.directory.open(project["project_id"])
    before = store.database.read_bytes()
    snapshot = client.get("/studio/api/snapshot", params={"project_id": store.project_id}, headers={"X-Studio-Read": "1"})
    assert snapshot.status_code == 200
    assert snapshot.json()["project"]["plan"]["state"] == "no_plan"
    assert snapshot.json()["project"]["evidence"]["object_count"] == 0
    assert store.database.read_bytes() == before
    assert client.get("/studio/api/snapshot", headers={"X-Studio-Read": "1", "Origin": "https://foreign.invalid"}).status_code == 403
    assert client.get("/studio/api/snapshot?unexpected=1", headers={"X-Studio-Read": "1"}).status_code == 400
    assert client.post("/studio/api/session", json={}).status_code == 200
    assert client.post("/studio/api/logout", json={}).status_code == 200
    assert client.get("/studio/api/snapshot", headers={"X-Studio-Read": "1"}).status_code == 401


def test_studio_accelerator_route_absent_and_project_bytes_stable(studio, tmp_path):
    engine, endpoint, client = studio
    login(endpoint, client)
    one = add_project(engine, tmp_path, "one")
    store = engine.directory.open(one["project_id"], write=True)
    payload = {"project_id": store.project_id, "expected_revision": 0, "config": {
        "purpose": "test retrieval", "action_classes": ["RETRIEVAL"], "expires_at": "NO_EXPIRY"}}
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    result = client.post("/studio/api/accelerator", json=payload)
    assert result.status_code == 404 and result.json()["error"] == "UNKNOWN_ROUTE"
    assert {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before


def test_studio_connector_and_job_routes_absent(studio, tmp_path):
    engine, endpoint, client = studio
    login(endpoint, client)
    record = add_project(engine, tmp_path, "one")
    store = engine.directory.open(record["project_id"], write=True)
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    for route in ("plugin", "revoke-plugin", "cancel-job"):
        response = client.post("/studio/api/" + route, json={"project_id": store.project_id})
        assert response.status_code == 404 and response.json()["error"] == "UNKNOWN_ROUTE"
    assert {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before
    snapshot = client.get("/studio/api/snapshot", params={"project_id": store.project_id}, headers={"X-Studio-Read": "1"}).json()
    assert snapshot["project"]["plugins"] == []
    assert snapshot["project"]["jobs"]["state"] == "not_initialized"
