"""Fixed Windows provider worker: one verified environment and resident model.

Only authenticated local engine workers can submit bounded JSON requests. The
process retains model state, never a project history, source index or result cache.
"""
from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from multiprocessing.connection import Listener
from pathlib import Path
from uuid import uuid4


def implementation():
    path = Path(__file__).with_name('provider_operations.py')
    spec = importlib.util.spec_from_file_location('evidence_lane_provider_operations', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def join_owner(name):
    api = ctypes.WinDLL('kernel32', use_last_error=True, winmode=0x800)
    api.OpenJobObjectW.argtypes, api.OpenJobObjectW.restype = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p], ctypes.c_void_p
    api.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    api.GetCurrentProcess.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenJobObjectW(0x1, False, name)
    if not handle:
        raise ValueError('PROVIDER_OWNER_UNAVAILABLE')
    try:
        if not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
            raise ValueError('PROVIDER_OWNER_BINDING_FAILED')
    finally:
        api.CloseHandle(handle)


def probe_result(module, manifest, startup):
    value = module.directml_probe(startup) if manifest['runtime_id'] == 'directml' else module.torch_probe(manifest['runtime_id'], startup)
    return value | {'runtime_id': manifest['runtime_id'], 'self_test': 'passed',
        'environment_digest': manifest['lock_sha256'], 'probe_id': str(uuid4()),
        'observed_at': datetime.now(UTC).isoformat(), 'reason': 'RESIDENT_PROVIDER_NUMERICAL_CHECK_PASSED',
        'installed_record_integrity': 'verified_at_worker_initialization'}


def serve():
    if os.name != 'nt':
        raise ValueError('PROVIDER_SERVER_PLATFORM')
    raw = sys.stdin.buffer.readline(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError('PROVIDER_SERVER_INPUT_BUDGET')
    startup = json.loads(raw)
    join_owner(startup['owner_group'])
    operations = implementation()
    probe = operations.probe_module()
    cache = {}
    with contextlib.redirect_stdout(sys.stderr):
        manifest = probe.verify_environment(Path(startup['manifest_path']), startup['manifest_sha256'])
        if startup['runtime_id'] != manifest['runtime_id']:
            raise ValueError('PROVIDER_RUNTIME_BINDING')
        observed = probe_result(probe, manifest, startup)
        startup['device_index'] = observed['device_index']
        if 'code_embed_text' in manifest.get('operations', []):
            operations.embed(startup | {'operation': 'code_embed_text',
                'arguments': startup['model'] | {'texts': ['Evidence Lane model warmup.']}}, manifest, probe, cache)
        else:
            operations.verify_asset(startup['model'])
        # The ready receipt covers the environment bytes used during warmup.
        probe.verify_environment(Path(startup['manifest_path']), startup['manifest_sha256'])
    with Listener(startup['address'], family='AF_PIPE', authkey=bytes.fromhex(startup['authkey'])) as listener:
        print(json.dumps({'status': 'ready', 'worker_id': startup['worker_id'], 'request_id': startup['request_id'],
            'runtime_manifest_sha256': startup['manifest_sha256'], 'probe': observed,
            'model_ready': bool(cache), 'worker_pid': os.getpid()}), flush=True)
        while True:
            connection = None
            try:
                connection = listener.accept()
                request = json.loads(connection.recv_bytes(1_048_576))
                if request.get('worker_id') != startup['worker_id']:
                    raise ValueError('PROVIDER_WORKER_BINDING')
                operation = request['operation']
                with contextlib.redirect_stdout(sys.stderr):
                    if operation == 'probe':
                        value = probe_result(probe, manifest, startup)
                    else:
                        if operation not in manifest.get('operations', []):
                            raise ValueError('PROVIDER_OPERATION_UNAVAILABLE')
                        arguments = request['arguments']
                        if any(arguments.get(key) != startup['model'].get(key)
                               for key in ('model_id', 'model_path', 'model_files', 'asset_identity')):
                            raise ValueError('PROVIDER_MODEL_BINDING')
                        invocation = startup | {'operation': operation, 'arguments': arguments}
                        value = (operations.embed(invocation, manifest, probe, cache) if operation == 'code_embed_text'
                            else operations.sibling_module('provider_ocr').run(invocation, manifest, probe, operations.verify_asset))
                        value['compute']['provider_worker_id'] = startup['worker_id']
                        value['compute']['package_integrity_basis'] = 'hash_verified_resident_worker_initialization'
                response = {'status': 'ok', 'result': value, 'request_id': request['request_id'],
                    'worker_id': startup['worker_id'], 'operation': operation}
                payload = json.dumps(response, allow_nan=False).encode()
                if len(payload) > 4_194_304:
                    raise ValueError('PROVIDER_OUTPUT_BUDGET')
                connection.send_bytes(payload)
            except Exception:  # noqa: BLE001 - no local source text, credential or vendor exception is returned
                if connection is not None:
                    with contextlib.suppress(Exception):
                        connection.send_bytes(b'{"status":"error","code":"PROVIDER_OPERATION_FAILED"}')
            finally:
                if connection is not None:
                    connection.close()
                request = response = value = payload = arguments = invocation = None


if __name__ == '__main__':
    try:
        serve()
    except Exception:  # noqa: BLE001 - the owning engine receives only a finite initialization outcome
        print('{"status":"error","code":"PROVIDER_WORKER_START_FAILED"}', flush=True)
        sys.exit(1)
