from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from evidence_lane_plugin.connections import ClientRouter, ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.sdk import EvidenceLaneClient
from evidence_lane_plugin.writers import WriterLease


def projects(engine, tmp_path):
    result = []
    for name in ("alpha", "beta"):
        source = tmp_path / name
        source.mkdir()
        result.append(engine.directory.register(tmp_path / f"{name}-state", source_root=source,
                                                 create=True, read_only=False)["project_id"])
    return result


def test_two_native_clients_cannot_cross_selected_project_contexts(tmp_path):
    engine = Engine(tmp_path / "runtime")
    alpha, beta = projects(engine, tmp_path)
    with engine, LocalEndpoint(engine), LocalTransport(
        engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=alpha)]),
    ) as first, LocalTransport(
        engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=beta)]),
    ) as second:
        first_result = EvidenceLaneClient(first).call("client_context")
        second_result = EvidenceLaneClient(second).call("client_context")
        assert first_result.status == second_result.status == 'ok'
        assert first_result.result["client_id"] != second_result.result["client_id"]
        assert [item['project_id'] for item in first_result.result['projects']] == [alpha]
        assert [item['project_id'] for item in second_result.result['projects']] == [beta]
        assert EvidenceLaneClient(first).call("project_status", project_id=alpha).result['project_id'] == alpha
        assert EvidenceLaneClient(second).call("project_status", project_id=beta).result['project_id'] == beta
        assert EvidenceLaneClient(first).call("project_status", project_id=beta).error.code == "PROJECT_NOT_SELECTED"
        assert EvidenceLaneClient(second).call("project_status", project_id=alpha).error.code == "PROJECT_NOT_SELECTED"


def test_write_grants_are_checked_on_each_request_and_revocable(tmp_path):
    engine = Engine(tmp_path / "runtime")
    alpha, _ = projects(engine, tmp_path)
    token, session = engine.clients.connect(ConnectRequest(projects=[
        ProjectSelection(project_id=alpha, permissions=["read", "write"]),
    ]))
    assert engine.clients.context(session, alpha, "write").client_id == session.client_id
    access = ProjectAccess(engine.directory.open(alpha, write=True))
    access.revoke(session.projects[alpha].grant_id)
    with pytest.raises(LaneError) as error:
        engine.clients.context(session, alpha, "write")
    assert error.value.code == "PERMISSION_DENIED"
    engine.clients.disconnect(token)
    with pytest.raises(LaneError):
        engine.clients.authenticate(token)


def test_expired_sessions_reject_work_and_do_not_occupy_capacity(tmp_path):
    current = datetime(2026, 9, 5, tzinfo=UTC)
    engine = Engine(tmp_path / "runtime")
    router = ClientRouter(engine.directory, clock=lambda: current, max_clients=1)
    token, _ = router.connect(ConnectRequest(claimed_task_id="unverified-label"))
    with pytest.raises(LaneError):
        router.connect(ConnectRequest())
    assert router.status()[0]["identity_evidence"] == "client_claim_only"
    current += timedelta(hours=13)
    with pytest.raises(LaneError):
        router.authenticate(token)
    router.connect(ConnectRequest())


def test_read_only_selection_does_not_change_project_database(tmp_path):
    engine = Engine(tmp_path / "runtime")
    alpha, _ = projects(engine, tmp_path)
    store = engine.directory.open(alpha)
    before = {path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*')
              if path.suffix in {'.sqlite', '.sqlite3'}}
    assert store.database.relative_to(store.root) in before
    assert len(before) == 1 + len(store.lane_catalog())
    token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=alpha)]))
    engine.clients.context(session, alpha, "read")
    engine.clients.disconnect(token)
    assert {path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*')
            if path.suffix in {'.sqlite', '.sqlite3'}} == before


def test_client_grant_creation_cannot_write_while_a_job_owns_the_project(tmp_path):
    engine = Engine(tmp_path / 'runtime')
    alpha, _ = projects(engine, tmp_path)
    store = engine.directory.open(alpha, write=True)
    with WriterLease(store, engine.instance_id):
        with pytest.raises(LaneError) as error:
            engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=alpha, permissions=['read', 'write'])]))
        assert error.value.code == 'PROJECT_WRITER_BUSY'
        with store.lane('receipts').connection(read_only=True) as connection:
            assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='access_grants'").fetchone()
        # Read-only native clients remain usable during the active writer.
        token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=alpha)]))
        assert engine.clients.context(session, alpha, 'read').project_id == alpha
        engine.clients.disconnect(token)


def test_disconnect_denies_work_immediately_and_defers_grant_cleanup_until_writer_releases(tmp_path):
    engine = Engine(tmp_path / 'runtime')
    alpha, _ = projects(engine, tmp_path)
    store = engine.directory.open(alpha, write=True)
    token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=alpha, permissions=['read', 'write'])]))
    grant = session.projects[alpha].grant_id
    with WriterLease(store, engine.instance_id):
        engine.clients.disconnect(token)
        with pytest.raises(LaneError):
            engine.clients.authenticate(token)
        with pytest.raises(LaneError):
            engine.clients.context(session, alpha, 'write')
        with store.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute('SELECT revoked_at FROM access_grants WHERE grant_id=?', (grant,)).fetchone()[0] is None
        assert engine.clients.revoke_pending()['pending'] == 1
    assert engine.clients.revoke_pending() == {'completed': 1, 'pending': 0}
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute('SELECT revoked_at FROM access_grants WHERE grant_id=?', (grant,)).fetchone()[0]
