"""Actual CUDA model residency and both Code queries through current engine APIs."""
import contextlib
import json
import os
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.accelerators import ProjectAccess, probe_nvidia_devices
from evidence_lane_plugin.code_semantic import EMBEDDING_COMPUTE
from evidence_lane_plugin.flash_authority import SessionFlashAuthority
from evidence_lane_plugin.optional_runtimes import OptionalRuntime
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.workers import WorkerPool

from . import test_code_model_runtime_v4 as model_tests
from . import test_code_profile_v4 as code_base

pytestmark = pytest.mark.skipif(not os.environ.get('EVI_CUDA_QUALIFICATION'), reason='Requires exact isolated CUDA environment and real compatible GPU.')


def test_real_resident_cuda_serves_multiple_workers_and_queries_without_project_read_writes(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_CODE_QUALIFICATION_ASSETS'])
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('HF_HUB_DISABLE_TELEMETRY', '1')
    contracts = root / 'plugins/evidence-lane-plugin/toolchains/providers'
    manifest = json.loads((contracts / 'cuda.json').read_text())
    runtime = OptionalRuntime.from_installation(root / '.work/optional-envs' / ('cuda-' + manifest['lock_sha256'][:16]), contracts)
    devices = probe_nvidia_devices()
    assert len(devices) == 1
    timings = []
    original_call = model_tests.call
    def measured(*args, **kwargs):
        start = time.monotonic()
        response = original_call(*args, **kwargs)
        if args[1] in {'code_semantic_query', 'code_semantic_query_vec'} and response.status == 'ok':
            value = response.result['result']
            proof = value['worker_evidence']['compute']['worker_evidence']
            assert proof['selected_provider'] == 'NVIDIA_CUDA' and proof['resident_model_reused']
            timings.append({'action': args[1], 'seconds': time.monotonic() - start, 'proof': proof})
        return response
    monkeypatch.setattr(model_tests, 'call', measured)
    with contextlib.contextmanager(code_base.code_system.__wrapped__)(tmp_path) as system:
        engine, store, session = system
        SessionFlashAuthority().verify(registry=engine.registry)
        replacement = WorkerPool(tuple(engine.workers.operations.values()), workers=2)
        assert engine.replace_workers(replacement).quiescent
        engine.capabilities.runtimes = {'cuda': runtime}
        response = code_base.call(system, 'accelerator_configure', {'expected_revision': 0, 'config': {
            'requested_profile': 'nvidia', 'enabled_vendor_plugins': ['nvidia'], 'purpose': 'Isolated actual resident GPU qualification',
            'action_classes': ['RETRIEVAL'], 'device_id': devices[0].device_id, 'expires_at': 'NO_EXPIRY'}})
        assert response.status == 'ok', response.error
        context = ActionContext(session.client_id, store.project_id, frozenset({'read', 'write', 'tools', 'admin'}),
            authorize=lambda permission: ProjectAccess(store).authorize(session.client_id, permission))
        router = engine.registry.tool_router.compute
        started = time.monotonic()
        initial = router.select(EMBEDDING_COMPUTE, context)
        assert initial['selected_provider'] == 'CPU'
        deadline = time.monotonic() + 245
        while time.monotonic() < deadline:
            states = engine.provider_workers.status()
            assert states and states[0]['state'] not in {'unavailable', 'stopped'}, states
            if states[0]['state'] == 'ready':
                break
            time.sleep(.2)
        else:
            pytest.fail('The exact provider process did not finish its bounded warmup')
        warmup = time.monotonic() - started
        selected = router.select(EMBEDDING_COMPUTE, context)
        assert selected['selected_provider'] == 'NVIDIA_CUDA', selected
        invocation = router.invocation(EMBEDDING_COMPUTE, context, selected)
        futures = [invocation.submit('code_embed_text', {'texts': [text]}) for text in ['First shared worker query.', 'Second shared worker query.']]
        results = [future.result(timeout=30) for future in futures]
        for value in results:
            invocation.result(value)
            assert value['result']['compute']['resident_model_reused']
        assert len({value['worker_pid'] for value in results}) == 2
        model_tests.test_real_bge_embeddings_and_all_cosine_readers_preserve_project_bytes(system)
        with store.lane('local_code').connection(read_only=True) as db:
            rows = db.execute('SELECT manifest_object FROM code_embedding_run').fetchall()
        for row in rows:
            record = json.loads(store.lane('local_code').read_object(row['manifest_object']))
            assert record['device'] == 'cuda:0'
            assert record['compute']['selection']['provider_worker_id'] == selected['provider_worker_id']
        assert len(timings) == 2 and all(row['seconds'] < 30 for row in timings)
        # The connection credential never enters project databases or artifacts.
        binding = engine.provider_workers.binding('cuda', devices[0].device_id, selected['provider_worker_id'])
        secret = binding['authkey'].encode()
        assert all(secret not in path.read_bytes() for path in store.root.rglob('*') if path.is_file())
        status = engine.provider_workers.status()
    assert engine.phase == 'stopped'
    assert all(row['state'] == 'stopped' for row in engine.provider_workers.status())
    (root / '.work/verification/resident-cuda-result.json').write_text(json.dumps({
        'warmup_seconds': warmup, 'queries': timings, 'worker_status_before_close': status,
        'two_cpu_workers_shared_one_provider_model': True, 'project_reads_unchanged': True,
        'engine_stop_confirmed_provider_process_termination': True,
        'installed_native_execution_claimed': False}, indent=2) + '\n')
