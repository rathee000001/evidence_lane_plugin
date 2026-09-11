"""Compute admission and actual adapter boundaries; GPU observations are fixtures."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.accelerators import (
    AcceleratorConfig,
    AcceleratorService,
    DeviceObservation,
    RuntimeEvidence,
)
from evidence_lane_plugin.compute_routes import ComputeContract, ComputeRouter, binding
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.registry import ActionContext, ActionRegistry, ActionSpec, Contract
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.tool_routes import ToolRoute, admission_binding
from evidence_lane_plugin.writers import WriterLease


class Input(Contract):
    text: str


class Output(Contract):
    text: str


COMPUTE = ComputeContract('fixture_embed', 'RETRIEVAL', ('CPU', 'NVIDIA_CUDA', 'AMD_ROCM'), 1024)


@pytest.fixture
def system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    store = ProjectStore.create(tmp_path / 'state', source)
    access = ProjectAccess(store)
    access.initialize()
    permissions = frozenset({'read', 'tools', 'admin'})
    grant_id = access.issue('client', permissions, [source])
    current = datetime.now(UTC)
    monitor = CapabilityMonitor(requirements=(), device_probe=list, clock=lambda: current)
    engine = SimpleNamespace(directory=SimpleNamespace(open=lambda project_id: store), capabilities=monitor)
    router = ComputeRouter(engine)
    context = ActionContext('client', store.project_id, permissions,
        authorize=lambda permission: access.authorize('client', permission))
    service = AcceleratorService(store)
    return SimpleNamespace(store=store, access=access, grant_id=grant_id, monitor=monitor,
        router=router, context=context, service=service, current=current, engine=engine)


def configure(system, **changes):
    config = AcceleratorConfig.model_validate({'requested_profile': 'nvidia', 'enabled_vendor_plugins': ['nvidia'],
        'purpose': 'Bounded fixture embedding', 'action_classes': ['RETRIEVAL'], 'device_id': 'GPU-fixture',
        'expires_at': 'NO_EXPIRY'} | changes)
    with WriterLease(system.store, 'fixture') as lease:
        system.service.initialize(lease)
        return system.service.configure(config, system.context, lease, expected_revision=system.service.settings()['revision'])


def gpu(system, *, provider='NVIDIA_CUDA', supported=True, temperature=40):
    runtime_id = {'NVIDIA_CUDA': 'cuda', 'AMD_ROCM': 'rocm', 'DIRECTML': 'directml'}[provider]
    runtime = SimpleNamespace(runtime_id=runtime_id, manifest_sha256='b' * 64, lock_sha256='a' * 64,
        supports_operation=lambda operation: supported,
        accelerator_evidence=lambda probe: RuntimeEvidence(provider=provider, device_id='GPU-fixture',
            runtime_version='fixture-only', runtime_platform_version='fixture', environment_digest='a' * 64,
            observed_at=system.current.isoformat(), self_test='passed', probe_id='fixture-only', reason='No hardware claim'),
        worker_binding=lambda: {'runtime_id': runtime_id, 'manifest_sha256': 'b' * 64})
    device = DeviceObservation(device_id='GPU-fixture', device_index=0, vendor='nvidia' if runtime_id == 'cuda' else 'amd',
        name='fixture-only', total_vram_mib=8192, used_vram_mib=1024, temperature_c=temperature,
        throttle_active=False, source='nvidia_smi' if runtime_id == 'cuda' else 'amd_smi', observed_at=system.current.isoformat())
    system.monitor.runtimes = {runtime_id: runtime}
    system.monitor.device_probe = lambda: [device]
    system.monitor._provider_probes[(runtime_id, 'GPU-fixture')] = {'runtime_id': runtime_id, 'self_test': 'passed'}
    return runtime


def registry(system, handler):
    registry = ActionRegistry()
    registry.tool_router.compute = system.router
    registry.tool_router.observer = lambda identity, context: {'ready': True, 'tool_id': identity}
    spec = ActionSpec('fixture_embedding', 'Fixture compute adapter', Input, Output, handler,
        worker_operations=('fixture_embed',), tool_routes=(ToolRoute('fixture_embedding.owned', handler, compute=COMPUTE),))
    registry.register(spec)
    return registry, spec


def test_unconfigured_cpu_route_is_read_only_and_does_not_probe(system):
    system.monitor.device_probe = lambda: pytest.fail('CPU default must not start a driver probe')
    before = {p: p.read_bytes() for p in system.store.root.rglob('*') if p.is_file()}
    selected = system.router.select(COMPUTE, system.context)
    assert selected['selected_provider'] == 'CPU' and selected['settings_revision'] == 0
    assert selected['execution_state'] == 'not_executed'
    assert before == {p: p.read_bytes() for p in system.store.root.rglob('*') if p.is_file()}


def test_cpu_query_does_not_require_gpu_tools_permission(system):
    selected = system.router.select(COMPUTE, replace(system.context, permissions=frozenset({'read'})))
    assert selected['selected_provider'] == 'CPU'
    assert selected['fallback_reasons'] == ['COMPUTE_TOOLS_GRANT_REQUIRED']


@pytest.mark.parametrize('provider', ['NVIDIA_CUDA', 'AMD_ROCM'])
def test_route_admission_and_handler_receive_same_measured_binding(system, provider):
    configure(system, **({'requested_profile': 'amd', 'enabled_vendor_plugins': ['amd']} if provider == 'AMD_ROCM' else {}))
    gpu(system, provider=provider)
    calls = []
    def handler(context, request):
        calls.append(context.computation.arguments('fixture_embed', {'text': request.text}))
        return {'text': request.text}
    catalog, spec = registry(system, handler)
    selected = catalog.tool_router.resolve(spec, system.context, arguments=Input(text='hello'))
    assert selected['compute']['selected_provider'] == provider
    admitted = replace(system.context, tool_admission=admission_binding(selected))
    output, receipt = catalog.execute_attributed(spec.name, {'text': 'hello'}, admitted)
    assert output == {'text': 'hello'} and len(calls) == 1
    assert calls[0]['_compute']['selected_provider'] == provider
    assert receipt['selection']['compute']['execution_state'] == 'not_executed'
    assert not receipt['dependency_execution_claimed']


@pytest.mark.parametrize('fault', ['settings', 'revocation', 'heat', 'runtime'])
def test_admitted_compute_cannot_change_before_or_during_work(system, fault):
    configure(system)
    runtime = gpu(system)
    invocation = system.router.invocation(COMPUTE, system.context, system.router.select(COMPUTE, system.context))
    invocation.arguments('fixture_embed', {})
    if fault == 'settings':
        configure(system, requested_profile='cpu')
    elif fault == 'revocation':
        with WriterLease(system.store, 'fixture') as lease:
            system.access.revoke(system.grant_id, writer=lease)
    elif fault == 'heat':
        gpu(system, temperature=100)
        system.monitor._snapshot['devices'] = []
    else:
        runtime.manifest_sha256 = 'c' * 64
    with pytest.raises(LaneError):
        invocation.check()


def test_probe_only_environment_and_unsupported_provider_have_visible_cpu_fallbacks(system):
    configure(system)
    gpu(system, supported=False)
    selected = system.router.select(COMPUTE, system.context)
    assert selected['selected_provider'] == 'CPU'
    assert 'RUNTIME_OPERATION_NOT_INSTALLED' in selected['fallback_reasons']
    configure(system, requested_profile='amd', enabled_vendor_plugins=['amd'])
    gpu(system, provider='DIRECTML')
    system.monitor._snapshot['devices'] = []
    selected = system.router.select(COMPUTE, system.context)
    assert selected['selected_provider'] == 'CPU'
    assert 'OPERATION_PROVIDER_UNSUPPORTED' in selected['fallback_reasons']


def test_provider_failure_is_not_replayed_and_result_cannot_claim_another_provider(system):
    configure(system)
    gpu(system)
    invoked = []
    def handler(context, request):
        invoked.append(context.computation.arguments('fixture_embed', {'text': request.text}))
        context.computation.result({'status': 'error'})
    catalog, spec = registry(system, handler)
    with pytest.raises(LaneError) as failure:
        catalog.execute_attributed(spec.name, {'text': 'private'}, system.context)
    assert failure.value.code == 'COMPUTE_WORKER_FAILED' and len(invoked) == 1
    assert not failure.value.details['tool_execution']['automatic_retry']
    invocation = system.router.invocation(COMPUTE, system.context, catalog.tool_router.resolve(spec, system.context)['compute'])
    with pytest.raises(LaneError) as failure:
        invocation.result({'status': 'ok', 'result': {'compute': {'selected_provider': 'CPU', 'execution_state': 'executed'}}})
    assert failure.value.code == 'COMPUTE_RESULT_UNBOUND'


def test_contract_must_name_owned_worker_and_worker_inputs_cannot_override_compute(system):
    def handler(context, request):
        return {'text': request.text}
    catalog, spec = registry(system, handler)
    bad = replace(spec, tool_routes=(replace(spec.tool_routes[0], compute=replace(COMPUTE, worker_operation='other')),))
    with pytest.raises(LaneError):
        ActionRegistry().register(bad)
    selection = system.router.select(COMPUTE, system.context)
    invocation = system.router.invocation(COMPUTE, system.context, selection)
    with pytest.raises(LaneError):
        invocation.arguments('fixture_embed', {'_compute': {'selected_provider': 'NVIDIA_CUDA'}})
    assert binding(selection) == admission_binding(catalog.tool_router.resolve(spec, system.context))['compute']


def test_settings_tamper_cannot_authorize_gpu(system):
    configure(system)
    gpu(system)
    configure(system, expires_at=(datetime.now(UTC) + timedelta(seconds=1)).isoformat())
    with WriterLease(system.store, 'fixture') as lease, lease.transaction('receipts') as db:
        db.execute("UPDATE accelerator_settings SET digest=? WHERE revision=2", ('0' * 64,))
    with pytest.raises(LaneError) as error:
        system.router.select(COMPUTE, system.context)
    assert error.value.code == 'ACCELERATOR_SETTINGS_INTEGRITY'


def test_finishing_gpu_warmup_does_not_switch_an_admitted_cpu_operation(system):
    configure(system)
    seen = []
    def handler(context, request):
        seen.append(context.computation.arguments('fixture_embed', {})['_compute']['selected_provider'])
        return {'text': request.text}
    catalog, spec = registry(system, handler)
    admitted = catalog.tool_router.resolve(spec, system.context)
    assert admitted['compute']['selected_provider'] == 'CPU'
    gpu(system)
    # A newly appearing device becomes visible after the bounded negative inventory cache.
    system.monitor.clock = lambda: system.current + timedelta(seconds=3)
    catalog.execute_attributed(spec.name, {'text': 'still the admitted CPU operation'},
        replace(system.context, tool_admission=admission_binding(admitted)))
    assert seen == ['CPU']
    assert catalog.tool_router.resolve(spec, system.context)['compute']['selected_provider'] == 'NVIDIA_CUDA'


def test_provider_warmup_is_reported_before_any_model_execution(system):
    configure(system)
    gpu(system)
    system.monitor.provider_workers = SimpleNamespace(prepare=lambda *args: 'prewarming',
        identity=lambda *args: None, status=list)
    selected = system.router.select(COMPUTE, system.context)
    assert selected['selected_provider'] == 'CPU'
    assert 'PROVIDER_OPERATION_PREWARMING' in selected['fallback_reasons']
