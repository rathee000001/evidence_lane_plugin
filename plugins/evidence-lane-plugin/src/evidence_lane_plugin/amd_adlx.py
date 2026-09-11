"""Read-only Windows ADLX C ABI, isolated from the persistent engine process.

Method slots and signatures follow AMD ADLX SDK commit
d9f04a9bba022d6cf6333f005dd540b4ad19fb63, ISystem{,2}.h and
IPerformanceMonitoring.h. No tuning, tracking or configuration method is called.
"""
from __future__ import annotations

import ctypes as c
import json
import math
import os
from pathlib import Path

SDK_VERSION = (1 << 48) | (5 << 32) | 124
P = c.c_void_p


class AdlxUnavailable(Exception):
    pass


class Luid(c.Structure):
    _fields_ = [('low', c.c_uint32), ('high', c.c_int32)]


def method(pointer, slot, result, *types):
    if not pointer:
        raise AdlxUnavailable('ADLX_INTERFACE_UNAVAILABLE')
    table = c.cast(pointer, c.POINTER(c.POINTER(P))).contents
    return c.WINFUNCTYPE(result, P, *types)(table[slot])


def output(pointer, slot, kind, *arguments, argument_types=()):
    value = kind()
    code = method(pointer, slot, c.c_int32, *argument_types, c.POINTER(kind))(
        pointer, *arguments, c.byref(value))
    if code != 0:
        raise AdlxUnavailable('ADLX_METRIC_UNAVAILABLE')
    return value


def release(pointer):
    if pointer:
        method(pointer, 1, c.c_int32)(pointer)


def read_metrics(library):
    initialize, terminate = library.ADLXInitialize, library.ADLXTerminate
    initialize.argtypes, initialize.restype = [c.c_uint64, c.POINTER(P)], c.c_int32
    terminate.argtypes, terminate.restype = [], c.c_int32
    system = P()
    if initialize(SDK_VERSION, c.byref(system)) != 0 or not system:
        raise AdlxUnavailable('ADLX_INITIALIZATION_UNAVAILABLE')
    gpus, service = P(), P()
    rows = []
    try:
        gpus = output(system, 1, P)
        service = output(system, 9, P)
        count = method(gpus, 3, c.c_uint32)(gpus)
        if not 0 <= count <= 32:
            raise AdlxUnavailable('ADLX_DEVICE_BUDGET')
        for index in range(count):
            gpu, gpu2, metrics = P(), P(), P()
            try:
                gpu = output(gpus, 11, P, index, argument_types=(c.c_uint32,))
                gpu2 = output(gpu, 2, P, 'IADLXGPU2', argument_types=(c.c_wchar_p,))
                luid = output(gpu2, 34, Luid)
                metrics = output(service, 18, P, gpu, argument_types=(P,))
                total = output(gpu, 11, c.c_uint32).value
                used = output(metrics, 12, c.c_int32).value
                temperature = output(metrics, 7, c.c_double).value
                if not math.isfinite(temperature) or not -50 <= temperature <= 200 or not 0 <= used <= total:
                    raise AdlxUnavailable('ADLX_METRIC_INVALID')
                rows.append({'device_id': f'DXGI-{luid.high & 0xFFFFFFFF:08x}{luid.low:08x}',
                    'total_vram_mib': total, 'used_vram_mib': used, 'temperature_c': math.ceil(temperature),
                    'throttle_active': None, 'telemetry_limits': ['driver_throttle_state_not_exposed_by_ADLX_metrics']})
            except AdlxUnavailable:
                # A GPU without current metrics remains in DXGI inventory with
                # unknown budgets. Never copy another adapter's measurements.
                continue
            finally:
                for pointer in (metrics, gpu2, gpu):
                    release(pointer)
        return rows
    finally:
        release(service)
        release(gpus)
        terminate()


def observe():
    if os.name != 'nt' or c.sizeof(P) != 8:
        raise AdlxUnavailable('ADLX_WINDOWS_AMD64_REQUIRED')
    kernel = c.WinDLL('kernel32', use_last_error=True, winmode=0x800)
    directory = c.create_unicode_buffer(32768)
    kernel.GetSystemDirectoryW.argtypes = [c.c_wchar_p, c.c_uint32]
    kernel.GetSystemDirectoryW.restype = c.c_uint32
    size = kernel.GetSystemDirectoryW(directory, len(directory))
    if not 0 < size < len(directory):
        raise AdlxUnavailable('ADLX_SYSTEM_DIRECTORY_UNAVAILABLE')
    # The AMD display driver owns this DLL. Do not search cwd, PATH or a
    # caller-provided location, and never initialize with an incompatible driver.
    path = Path(directory.value) / 'amdadlx64.dll'
    return read_metrics(c.CDLL(str(path), winmode=0x900))


def main():
    try:
        result = {'status': 'ok', 'devices': observe(), 'source': 'amd_adlx'}
    except (AdlxUnavailable, OSError, AttributeError, ValueError):
        result = {'status': 'unavailable', 'devices': [], 'source': 'amd_adlx'}
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
