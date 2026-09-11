"""Bounded engine-owned provider processes shared by its lane OS workers."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import LaneError
from .process_ownership import ChildProcessGroup, ExtendedLimits, kernel
from .provider_client import call_provider_worker
from .shared_tool_assets import resolve_shared_asset


class ProviderWorkers:
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self._lock = threading.RLock()
        self._entries = {}
        self._closing = False

    def prepare(self, runtime, device, contract):
        key = (runtime.runtime_id, device.device_id)
        with self._lock:
            if self._closing:
                return 'closing'
            entry = self._entries.get(key)
            if entry is None:
                if len(self._entries) >= 4:
                    return 'capacity_exhausted'
                entry = {'state': 'prewarming', 'runtime': runtime, 'device': device, 'contract': contract,
                    'reason': 'PROVIDER_PREWARM_REQUESTED', 'last_probe_refresh': 'not_requested',
                    'worker_id': str(uuid4()), 'process': None, 'group': None, 'threads': [],
                    'binding': None, 'probe': None, 'active': 0, 'completed': 0, 'failed': 0, 'refreshing': False}
                self._entries[key] = entry
                thread = threading.Thread(target=self._start, args=(entry,), daemon=True, name='evidence-lane-provider-prewarm')
                entry['threads'].append(thread)
                thread.start()
            elif entry['state'] == 'ready' and entry['active'] == 0 and not entry['refreshing']:
                age = (datetime.now(UTC) - datetime.fromisoformat(entry['probe']['observed_at'])).total_seconds()
                if age > 240:
                    entry['refreshing'] = True
                    thread = threading.Thread(target=self._refresh, args=(entry,), daemon=True, name='evidence-lane-provider-probe')
                    entry['threads'].append(thread)
                    thread.start()
            return entry['state']

    def _save_probe(self, entry, probe):
        runtime, device = entry['runtime'], entry['device']
        if (probe.get('runtime_id') != runtime.runtime_id or probe.get('self_test') != 'passed'
                or probe.get('environment_digest') != runtime.lock_sha256 or probe.get('device_id') != device.device_id):
            raise ValueError('Provider readiness binding changed')
        with self._lock:
            if self._closing:
                return
            entry['probe'] = dict(probe)
        with self.capabilities._lock:
            self.capabilities._provider_probes[(runtime.runtime_id, device.device_id)] = dict(probe)

    def _start(self, entry):
        runtime, device, contract = entry['runtime'], entry['device'], entry['contract']
        process, group = None, None
        try:
            folder, asset = resolve_shared_asset('embedding_snapshot' if contract.provider_operation == 'code_embed_text' else 'rapidocr_models')
            model = {'model_path': str(folder), 'model_files': asset['files'], 'asset_identity': asset['files_sha256']}
            if contract.provider_operation == 'code_embed_text':
                model['model_id'] = 'BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'
            binding = {'address': r'\\.\pipe\EvidenceLaneProvider-v4-' + entry['worker_id'],
                'authkey': os.urandom(32).hex(), 'worker_id': entry['worker_id']}
            nonce = str(uuid4())
            request = runtime.worker_binding() | binding | {'request_id': nonce, 'device_id': device.device_id,
                'device_index': device.device_index, 'required_vram_mib': contract.required_vram_mib, 'model': model}
            with self._lock:
                if self._closing:
                    return
                group = ChildProcessGroup()
                group.start()
                limits = ExtendedLimits()
                limits.basic.flags = 0x2000 | 0x8
                limits.basic.active_process_limit = 2  # Windows venv redirector plus its actual interpreter.
                if os.name != 'nt' or not kernel().SetInformationJobObject(group.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    raise ValueError('Provider owner unavailable')
                environment = {key: value for key, value in os.environ.items() if key.upper() not in {
                    'CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES', 'PYTHONPATH', 'PYTHONHOME'}}
                environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                    HF_HUB_DISABLE_PROGRESS_BARS='1')
                process = subprocess.Popen([str(runtime.python), '-I', '-B', str(Path(__file__).with_name('provider_server.py'))],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                entry.update(process=process, group=group, binding=binding)
                if not kernel().AssignProcessToJobObject(group.handle, int(process._handle)):
                    raise ValueError('Provider child ownership unavailable')
                request['owner_group'] = group.name
            def drain_errors():
                consumed = 0
                while block := process.stderr.read(4096):
                    consumed += len(block)
                    if consumed > 65_536:
                        process.kill()
                        break
            stderr = threading.Thread(target=drain_errors, daemon=True, name='evidence-lane-provider-errors')
            stderr.start()
            with self._lock:
                entry['threads'].append(stderr)
            timer = threading.Timer(240, lambda: process.kill() if process.poll() is None and entry['state'] == 'prewarming' else None)
            timer.start()
            try:
                process.stdin.write(json.dumps(request).encode() + b'\n')
                process.stdin.flush()
                process.stdin.close()
                raw = process.stdout.readline(65_537)
                if len(raw) > 65_536:
                    raise ValueError('Provider readiness exceeds byte budget')
                ready = json.loads(raw)
                if (ready.get('status') != 'ready' or ready.get('worker_id') != entry['worker_id']
                        or ready.get('request_id') != nonce or ready.get('runtime_manifest_sha256') != runtime.manifest_sha256
                        or (contract.provider_operation == 'code_embed_text' and ready.get('model_ready') is not True)):
                    raise ValueError('Provider readiness is unbound')
                self._save_probe(entry, ready['probe'])
                with self._lock:
                    if not self._closing:
                        entry['state'] = 'ready'
                        entry['reason'] = 'BOUND_PROVIDER_PREWARM_PASSED'
            finally:
                timer.cancel()
        except (LaneError, OSError, ValueError, KeyError, TypeError):
            with self._lock:
                entry['state'] = 'unavailable'
                entry['reason'] = 'PROVIDER_PREWARM_FAILED'
            if group:
                group.close()
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def _refresh(self, entry):
        try:
            value = call_provider_worker(entry['binding'], 'probe', {}, timeout=10)
            self._save_probe(entry, value)
            with self._lock:
                entry['last_probe_refresh'] = 'passed'
        except (LaneError, OSError, ValueError):
            with self._lock:
                entry['last_probe_refresh'] = 'failed'  # Keep the actual probe time; expiry still applies.
        finally:
            with self._lock:
                entry['refreshing'] = False

    def _ready(self, runtime_id, device_id):
        entry = self._entries.get((runtime_id, device_id))
        if (entry is None or entry['state'] != 'ready' or entry['process'] is None
                or entry['process'].poll() is not None or self._closing):
            return None
        return entry

    def identity(self, runtime_id, device_id):
        with self._lock:
            entry = self._ready(runtime_id, device_id)
            return entry['worker_id'] if entry else None

    def binding(self, runtime_id, device_id, expected):
        with self._lock:
            entry = self._ready(runtime_id, device_id)
            if entry is None or entry['worker_id'] != expected:
                raise LaneError('PROVIDER_WORKER_UNAVAILABLE', 'The admitted provider process is unavailable; no replay was issued.')
            return dict(entry['binding'])

    def track(self, future, selection):
        with self._lock:
            entry = self._ready(selection['runtime_id'], selection['device_id'])
            if entry is None:
                return
            entry['active'] += 1
        def completed(value):
            try:
                response = value.result()
                proof = response.get('result', {}).get('compute', {})
                if (response.get('status') == 'ok' and proof.get('provider_worker_id') == entry['worker_id']
                        and proof.get('execution_state') == 'executed' and entry['probe']):
                    self._save_probe(entry, entry['probe'] | {'observed_at': datetime.now(UTC).isoformat(),
                        'probe_id': str(uuid4()), 'reason': 'RESIDENT_MODEL_FORWARD_PASSED'})
                elif response.get('status') != 'ok':
                    with self._lock:
                        entry['failed'] += 1
            except Exception:  # noqa: BLE001 - the operation's own future retains its error
                with self._lock:
                    entry['failed'] += 1
            finally:
                with self._lock:
                    entry['active'] -= 1
                    entry['completed'] += 1
        future.add_done_callback(completed)

    def status(self):
        with self._lock:
            return [{'runtime_id': entry['runtime'].runtime_id, 'device_id': entry['device'].device_id,
                'worker_id': entry['worker_id'], 'state': entry['state'] if entry['process'] is None
                    or entry['process'].poll() is None else 'stopped',
                'model_residency': ('cached' if entry['contract'].provider_operation == 'code_embed_text' else 'per_request')
                    if entry['state'] == 'ready' and entry['process'].poll() is None else 'not_ready',
                'reason': entry.get('reason', 'PROVIDER_STATE_OBSERVED') if entry['process'] is None
                    or entry['process'].poll() is None else 'OWNED_PROVIDER_PROCESS_EXITED',
                'probe_observed_at': entry['probe'].get('observed_at') if entry['probe'] else None,
                'last_probe_refresh': entry.get('last_probe_refresh', 'not_requested'),
                'completion_basis': 'finished_lane_worker_futures',
                'active_requests': entry['active'], 'completed_requests': entry['completed'], 'failed_requests': entry['failed']}
                for entry in self._entries.values()]

    def close(self, *, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self._lock:
            self._closing = True
            entries = list(self._entries.values())
            for entry in entries:
                if entry['group']:
                    entry['group'].close()
        for entry in entries:
            for thread in entry['threads']:
                remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
                thread.join(remaining)
                if thread.is_alive():
                    return False
            process = entry['process']
            if process:
                remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    return False
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
            entry['state'] = 'stopped'
        return True
