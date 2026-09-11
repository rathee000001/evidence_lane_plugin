"""AMD adapter identity and C ABI fixtures; these do not qualify AMD hardware."""
import ctypes as c
import json
from types import SimpleNamespace

import pytest
from evidence_lane_plugin import amd_adlx, provider_probe
from evidence_lane_plugin.accelerators import DeviceObservation, probe_amd_devices

from .test_compute_routes_v4 import COMPUTE, configure, gpu
from .test_compute_routes_v4 import system as system  # noqa: PLC0414


def test_adlx_readonly_abi_joins_luid_and_preserves_unknown_throttle():
    objects, functions = [], []
    released, lifecycle = [], []

    def interface(slots, size):
        table = (c.c_void_p * size)()
        for slot, result_type, types, callback in slots:
            function = c.WINFUNCTYPE(result_type, c.c_void_p, *types)(callback)
            functions.append(function)
            table[slot] = c.cast(function, c.c_void_p)
        value = (c.c_void_p * 1)(c.cast(table, c.c_void_p))
        objects.extend([table, value])
        return c.cast(value, c.c_void_p)

    def put(kind, value):
        def callback(pointer, target):
            target.contents.value = value
            return 0
        return callback

    def ref_slots(name):
        return [(1, c.c_int32, (), lambda pointer: released.append(name) or 0)]

    def luid(pointer, target):
        target.contents.low, target.contents.high = 0x11223344, -1
        return 0

    gpu2 = interface(ref_slots('gpu2') + [(34, c.c_int32, (c.POINTER(amd_adlx.Luid),), luid)], 35)
    gpu1 = interface(ref_slots('gpu') + [
        (2, c.c_int32, (c.c_wchar_p, c.POINTER(c.c_void_p)),
            lambda pointer, name, target: (setattr(target.contents, 'value', gpu2.value) or 0) if name == 'IADLXGPU2' else -1),
        (11, c.c_int32, (c.POINTER(c.c_uint32),), put(c.c_uint32, 8192))], 19)
    metrics = interface(ref_slots('metrics') + [
        (7, c.c_int32, (c.POINTER(c.c_double),), put(c.c_double, 42.2)),
        (12, c.c_int32, (c.POINTER(c.c_int32),), put(c.c_int32, 1024))], 15)
    gpulist = interface(ref_slots('gpus') + [
        (3, c.c_uint32, (), lambda pointer: 1),
        (11, c.c_int32, (c.c_uint32, c.POINTER(c.c_void_p)),
            lambda pointer, index, target: (setattr(target.contents, 'value', gpu1.value) or 0) if index == 0 else -1)], 13)
    service = interface(ref_slots('service') + [
        (18, c.c_int32, (c.c_void_p, c.POINTER(c.c_void_p)),
            lambda pointer, selected, target: (setattr(target.contents, 'value', metrics.value) or 0) if selected == gpu1.value else -1)], 23)
    system = interface([(1, c.c_int32, (c.POINTER(c.c_void_p),), put(c.c_void_p, gpulist.value)),
        (9, c.c_int32, (c.POINTER(c.c_void_p),), put(c.c_void_p, service.value))], 12)

    def initialize(version, target):
        assert version == amd_adlx.SDK_VERSION
        c.cast(target, c.POINTER(c.c_void_p)).contents.value = system.value
        lifecycle.append('initialize')
        return 0

    library = SimpleNamespace(ADLXInitialize=initialize, ADLXTerminate=lambda: lifecycle.append('terminate') or 0)
    assert amd_adlx.read_metrics(library) == [{'device_id': 'DXGI-ffffffff11223344',
        'total_vram_mib': 8192, 'used_vram_mib': 1024, 'temperature_c': 43,
        'throttle_active': None, 'telemetry_limits': ['driver_throttle_state_not_exposed_by_ADLX_metrics']}]
    assert released == ['metrics', 'gpu2', 'gpu', 'service', 'gpus']
    assert lifecycle == ['initialize', 'terminate']


@pytest.mark.parametrize('fault', [None, 'wrong_luid', 'duplicate', 'invalid_metric', 'timeout'])
def test_adlx_metrics_never_join_by_adapter_position(monkeypatch, fault):
    from datetime import UTC, datetime

    from evidence_lane_plugin import accelerators

    device = DeviceObservation(device_id='DXGI-0000000011223344', device_index=2, vendor='amd',
        name='fixture', total_vram_mib=8192, source='dxgi', observed_at=datetime.now(UTC).isoformat())
    monkeypatch.setattr(accelerators, 'probe_amd_dxgi_devices', lambda: [device])
    row = {'device_id': device.device_id, 'total_vram_mib': 8192, 'used_vram_mib': 1024,
        'temperature_c': 42, 'throttle_active': None}
    if fault == 'wrong_luid':
        row['device_id'] = 'DXGI-9999999911223344'
    if fault == 'invalid_metric':
        row['used_vram_mib'] = 9000

    def child(argv, **kwargs):
        assert argv[-1].endswith('amd_adlx.py') and kwargs['timeout'] == 5
        if fault == 'timeout':
            raise accelerators.subprocess.TimeoutExpired(argv, 5)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'status': 'ok',
            'devices': [row, row] if fault == 'duplicate' else [row]}).encode())

    monkeypatch.setattr(accelerators.subprocess, 'run', child)
    observed = probe_amd_devices()[0]
    assert observed.device_index == 2 and observed.device_id == device.device_id
    assert observed.throttle_active is None
    assert observed.source == ('amd_adlx' if fault is None else 'dxgi')
    assert observed.used_vram_mib == (1024 if fault is None else None)


@pytest.mark.parametrize('temperature', [42, None, 95])
def test_adlx_selection_enforces_measured_temperature_without_invented_throttle(system, temperature):
    configure(system, requested_profile='amd', enabled_vendor_plugins=['amd'])
    gpu(system, provider='AMD_ROCM')
    previous = system.monitor.device_probe()[0]
    system.monitor.device_probe = lambda: [previous.model_copy(update={'source': 'amd_adlx',
        'throttle_active': None, 'temperature_c': temperature})]
    selected = system.router.select(COMPUTE, system.context)
    assert selected['selected_provider'] == ('AMD_ROCM' if temperature == 42 else 'CPU')
    if temperature == 42:
        assert selected['decision']['throttle_active'] is None


def test_selected_hip_runtime_index_is_carried_to_the_owned_worker(system):
    configure(system, requested_profile='amd', enabled_vendor_plugins=['amd'])
    runtime = gpu(system, provider='AMD_ROCM')
    old = runtime.accelerator_evidence
    runtime.accelerator_evidence = lambda probe: old(probe).model_copy(update={'device_index': 3})
    selected = system.router.select(COMPUTE, system.context)
    assert selected['device_index'] == 3
    invocation = system.router.invocation(COMPUTE, system.context, selected)
    assert invocation.arguments('fixture_embed', {})['_compute']['device_index'] == 3


@pytest.mark.parametrize('fault', [None, 'duplicate', 'wrong_operation_index'])
def test_rocm_uses_hip_luid_to_resolve_a_different_dxgi_order(tmp_path, monkeypatch, fault):
    identity = 'DXGI-0000000011223344'
    monkeypatch.setattr(provider_probe, 'enumerate_dxgi', lambda: [{'device_id': identity, 'device_index': 2, 'vendor_id': 0x1002}])
    monkeypatch.setattr(provider_probe.sys, 'prefix', str(tmp_path))
    name = '_rocm_sdk_core/bin/amdhip64_7.dll'
    monkeypatch.setattr(provider_probe.importlib.metadata, 'distribution', lambda name: SimpleNamespace(
        files=['_rocm_sdk_core/bin/amdhip64_7.dll'], locate_file=lambda name: tmp_path / name))
    def properties(target, index):
        value = 0x11223344 if index == 0 or fault == 'duplicate' else 0x55667788
        c.memmove(c.cast(target, c.c_void_p).value + 272, value.to_bytes(8, 'little'), 8)
        return 0
    def load(path, **kwargs):
        assert path == str(tmp_path / name)
        return SimpleNamespace(hipGetDevicePropertiesR0600=properties)
    monkeypatch.setattr(provider_probe.ctypes, 'CDLL', load)
    request = {'device_id': identity, 'device_index': 2}
    if fault == 'wrong_operation_index':
        request['operation'] = 'code_embed_text'
    if fault:
        with pytest.raises(provider_probe.ProbeUnavailable, match='PROVIDER_DEVICE_IDENTITY_MISMATCH'):
            provider_probe.rocm_windows_index(SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 2)), request)
    else:
        assert provider_probe.rocm_windows_index(SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 2)), request) == 0
