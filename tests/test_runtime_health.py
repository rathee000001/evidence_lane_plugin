from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.runtime_health import CapabilityMonitor, ToolRequirement
from evidence_lane_plugin.sdk import ActionRequest, EvidenceLaneClient
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool
from evidence_lane_plugin.writers import WriterLease


def test_inventory_distinguishes_presence_from_execution_and_status_is_read_only(monkeypatch):
    calls = []
    monitor = CapabilityMonitor(requirements=(ToolRequirement("missing", ("v4_nonexistent_module",)),),
                                device_probe=lambda: calls.append("devices") or [])
    assert monitor.snapshot()["state"] == "not_probed"
    observed = monitor.refresh()
    assert observed["tools"][0]["state"] == "unavailable"
    assert observed["tools"][0]["execution_state"] == "not_executed"
    assert observed["tools"][1]["execution_state"] == "passed"
    for _ in range(3):
        snapshot = monitor.snapshot()
        snapshot["tools"].clear()
    assert len(monitor.snapshot()["tools"]) == 2
    assert calls == ["devices"]


def test_provider_observations_expire_without_new_status_side_effects():
    instant = datetime.now(UTC)

    class FixtureRuntime:
        runtime_id = "cuda"

        def probe(self, **kwargs):
            return {"runtime_id": "cuda", "self_test": "passed", "observed_at": instant.isoformat(),
                    "probe_id": "fixture", "device_id": kwargs["device_id"]}

    monitor = CapabilityMonitor(optional_runtimes=(FixtureRuntime(),), clock=lambda: instant)
    monitor.probe_provider("cuda", device_id="fixture-device")
    assert monitor.snapshot()["provider_probes"][0]["fresh_for_selection"]
    monitor.clock = lambda: instant + timedelta(seconds=301)
    assert not monitor.snapshot()["provider_probes"][0]["fresh_for_selection"]


def test_health_exposes_only_selected_project_jobs_and_current_client(tmp_path):
    monitor = CapabilityMonitor(device_probe=list)
    with Engine(tmp_path / "runtime", capabilities=monitor) as engine:
        first_source = tmp_path / "first-source"
        second_source = tmp_path / "second-source"
        first_source.mkdir()
        second_source.mkdir()
        first = engine.directory.register(tmp_path / "first", source_root=first_source, create=True, read_only=False)
        second = engine.directory.register(tmp_path / "second", source_root=second_source, create=True, read_only=False)
        for record in (first, second):
            store = engine.directory.open(record["project_id"], write=True)
            with WriterLease(store, engine.instance_id) as lease:
                queue = JobQueue(store)
                queue.initialize(lease)
                queue.enqueue(ActionRequest(action="fixture", project_id=store.project_id), "fixture", lease)
        selection = ConnectRequest(label="first only", projects=[ProjectSelection(project_id=first["project_id"], permissions=["read"])])
        with LocalEndpoint(engine), LocalTransport(engine.root, connection=selection) as transport:
            client = EvidenceLaneClient(transport)
            response = client.call("runtime_status", project_id=first["project_id"])
            assert response.status == "ok"
            result = response.result
            assert result["project"]["jobs"]["counts"] == {"queued": 1}
            assert result["workers"]["state"] == "not_configured"
            assert result["client"]["label"] == "first only"
            assert result['inventory']['host_observation']['trigger'] == 'engine_start'
            catalog = result['inventory']['tool_catalog']
            assert catalog['counts'] == {'retained': 103}
            assert not catalog['shared_bundle_ready']
            assert all(row['availability_reason'] for row in catalog['entries'])
            assert len(result['inventory']['providers']) == 4
            assert all(row['reason'] == 'NO_BOUND_PROVIDER_ENVIRONMENT' for row in result['inventory']['providers'])
            assert second["project_id"] not in json.dumps(result)
            denied = client.call("runtime_status", project_id=second["project_id"])
            assert denied.status == "error"


def test_engine_integrates_real_worker_results_and_shutdown(tmp_path):
    pool = WorkerPool((WorkerOperation("hash_text", "evidence_lane_plugin.worker_tasks", "hash_text"),), workers=1)
    with Engine(tmp_path, worker_pool=pool, capabilities=CapabilityMonitor(device_probe=list)) as engine:
        assert pool.status()["state"] == "ready"
        assert pool.submit("hash_text", {"text": "health"}).result(timeout=10)["status"] == "ok"
        # Future callbacks finish after result() wakes; close drains them before asserting counters.
        engine.begin_drain()
        assert not pool.status()["accepting"]
    status = pool.status()
    assert status["state"] == "stopped"
    assert status["succeeded_operations"] == 1
    assert status["failed_operations"] == 0


def test_missing_project_job_schema_is_reported_without_creating_it(tmp_path):
    with Engine(tmp_path / "runtime", capabilities=CapabilityMonitor(device_probe=list)) as engine:
        source = tmp_path / "source"
        source.mkdir()
        record = engine.directory.register(tmp_path / "state", source_root=source, create=True, read_only=False)
        database = engine.directory.open(record["project_id"]).database
        before = Path(database).read_bytes()
        request = ConnectRequest(projects=[ProjectSelection(project_id=record["project_id"], permissions=["read"])])
        with LocalEndpoint(engine), LocalTransport(engine.root, connection=request) as transport:
            result = EvidenceLaneClient(transport).call("runtime_status", project_id=record["project_id"])
            assert result.result["project"]["jobs"]["state"] == "not_initialized"
        assert Path(database).read_bytes() == before


def test_invalid_requirement_catalog_is_refused():
    with pytest.raises(ValueError):
        CapabilityMonitor(requirements=(ToolRequirement("a"), ToolRequirement("a")))


def test_empty_device_inventory_is_cached_and_status_never_refreshes():
    instant = datetime.now(UTC)
    calls = []
    monitor = CapabilityMonitor(device_probe=lambda: calls.append(True) or [], clock=lambda: instant)
    monitor.compute_inventory()
    monitor.compute_inventory()
    assert calls == [True]
    monitor.clock = lambda: instant + timedelta(seconds=3)
    monitor.snapshot()
    assert calls == [True]
    monitor.compute_inventory()
    assert calls == [True, True]


def test_stale_or_malformed_health_observations_never_become_fresh():
    from evidence_lane_plugin.accelerators import DeviceObservation

    instant = datetime.now(UTC)
    device = DeviceObservation(device_id='GPU-fixture', device_index=0, vendor='nvidia', name='fixture',
        source='nvidia_smi', observed_at=instant.isoformat())
    monitor = CapabilityMonitor(device_probe=lambda: [device], clock=lambda: instant)
    monitor.refresh()
    assert monitor.snapshot()['device_freshness'][0]['fresh_for_selection']
    monitor.clock = lambda: instant + timedelta(seconds=11)
    snapshot = monitor.snapshot()
    assert snapshot['observation_age_seconds'] == 11
    assert not snapshot['device_freshness'][0]['fresh_for_selection']
    # Health decoration must not alter the typed records consumed by compute admission.
    assert DeviceObservation.model_validate(snapshot['devices'][0]) == device
    monitor._provider_probes[('cuda', 'fixture')] = {'observed_at': 'invalid', 'self_test': 'passed'}
    assert not monitor.snapshot()['provider_probes'][0]['fresh_for_selection']


def test_provider_health_reports_owned_exit_without_secret_or_new_work():
    from evidence_lane_plugin.compute_routes import ComputeContract
    from evidence_lane_plugin.provider_workers import ProviderWorkers

    workers = ProviderWorkers(CapabilityMonitor(device_probe=list))
    workers._entries[('cuda', 'fixture')] = {
        'runtime': SimpleNamespace(runtime_id='cuda'), 'device': SimpleNamespace(device_id='fixture'),
        'worker_id': 'fixture-worker', 'process': SimpleNamespace(poll=lambda: 1), 'state': 'ready',
        'reason': 'BOUND_PROVIDER_PREWARM_PASSED', 'contract': ComputeContract('code_embed_text', 'RETRIEVAL', ('CPU',), 1024),
        'probe': {'observed_at': datetime.now(UTC).isoformat()}, 'active': 0, 'completed': 2, 'failed': 1,
        'binding': {'authkey': 'private-credential', 'address': 'private-address'}}
    status = workers.status()[0]
    assert status['state'] == 'stopped' and status['reason'] == 'OWNED_PROVIDER_PROCESS_EXITED'
    assert status['model_residency'] == 'not_ready'
    assert status['completed_requests'] == 2 and status['failed_requests'] == 1
    assert 'private-' not in json.dumps(status)


def test_provider_failed_prewarm_is_visible_and_not_retried(monkeypatch):
    from evidence_lane_plugin.compute_routes import ComputeContract
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.provider_workers import ProviderWorkers

    calls = []
    def unavailable(*args):
        calls.append(True)
        raise LaneError('FIXTURE_ASSET_UNAVAILABLE', 'private-path-not-disclosed')
    monkeypatch.setattr('evidence_lane_plugin.provider_workers.resolve_shared_asset', unavailable)
    workers = ProviderWorkers(CapabilityMonitor(device_probe=list))
    runtime = SimpleNamespace(runtime_id='cuda')
    device = SimpleNamespace(device_id='fixture')
    contract = ComputeContract('code_embed_text', 'RETRIEVAL', ('CPU',), 1024)
    workers.prepare(runtime, device, contract)
    assert workers.close(timeout=5)
    status = workers.status()[0]
    assert status['reason'] == 'PROVIDER_PREWARM_FAILED'
    assert workers.prepare(runtime, device, contract) == 'closing'
    assert calls == [True]
    assert 'private-path' not in json.dumps(status)
