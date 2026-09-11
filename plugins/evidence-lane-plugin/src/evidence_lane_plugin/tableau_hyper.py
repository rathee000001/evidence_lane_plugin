"""Owned, bounded Hyper worker; adapts the admitted data_toolchain Hyper API path."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .errors import LaneError
from .hashing import canonical_json_bytes, sha256_bytes
from .process_ownership import ChildProcessGroup, ExtendedLimits, codec_python_executable, kernel


def invoke_hyper(request, timeout_seconds=45):
    # Windows is the currently qualified bundled server platform. Other hosts
    # retain XML extraction and use a configured Windows backend for this route.
    if os.name != 'nt':
        raise LaneError('TABLEAU_HYPER_HOST_UNAVAILABLE', 'Use the supported Windows backend for native Hyper operations.')
    from .tableau_hyper_child import runtime_evidence
    expected = runtime_evidence()
    # Match the existing PDF/media worker boundary: the engine supplies its
    # configured import roots after ownership, independent of ambient PYTHONPATH.
    payload = dict(request)
    payload['engine_import_roots'] = [str(Path(path).resolve()) for path in sys.path
                                    if path and Path(path).is_dir()]
    data = canonical_json_bytes(payload)
    if len(data) > 25_165_824:
        raise LaneError('TABLEAU_HYPER_INPUT_BUDGET', 'Select a smaller Hyper input.')
    script = Path(__file__).with_name('tableau_hyper_child.py')
    executable = codec_python_executable()
    script_sha = sha256_bytes(script.read_bytes())
    executable_sha = sha256_bytes(executable.read_bytes())
    group, process = ChildProcessGroup(), None
    group.start()
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000 | 0x200 | 0x8
        limits.basic.active_process_limit = 3  # Helper, hyperd and bounded vendor startup child.
        limits.job_memory = 2_147_483_648
        if not kernel().SetInformationJobObject(group.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise LaneError('TABLEAU_HYPER_OWNER_UNAVAILABLE', 'Hyper needs its bounded owned process group.')
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith('PYTHON')}
        process = subprocess.Popen([str(executable), '-I', '-S', '-B', str(script)],
            cwd=script.parent, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not kernel().AssignProcessToJobObject(group.handle, int(process._handle)):
            raise LaneError('TABLEAU_HYPER_OWNER_UNAVAILABLE', 'The native Hyper process could not be owned.')
        output, overflow = {'stdout': bytearray(), 'stderr': bytearray()}, []

        def drain(name, budget):
            stream = getattr(process, name)
            try:
                while block := stream.read(65536):
                    if len(output[name]) + len(block) > budget:
                        overflow.append(name)
                        process.kill()
                        return
                    output[name].extend(block)
            finally:
                stream.close()

        def send():
            try:
                process.stdin.write(data)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

        threads = [threading.Thread(target=drain, args=('stdout', 16_777_216), daemon=True),
            threading.Thread(target=drain, args=('stderr', 4096), daemon=True), threading.Thread(target=send, daemon=True)]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            raise LaneError('TABLEAU_HYPER_TIMEOUT', 'The native extract exceeded its bounded execution time.') from None
        for thread in threads:
            thread.join(timeout=3)
        if any(thread.is_alive() for thread in threads) or overflow or process.returncode:
            raise LaneError('TABLEAU_HYPER_OPERATION_FAILED', 'The bounded native extract operation did not complete.')
        response = json.loads(output['stdout'])
        if (response['evidence'] != expected or runtime_evidence() != expected
                or sha256_bytes(script.read_bytes()) != script_sha
                or sha256_bytes(executable.read_bytes()) != executable_sha):
            raise LaneError('TABLEAU_HYPER_RUNTIME_CHANGED', 'The native Hyper files changed during execution.')
        return response
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        group.close()


def inspect_hyper(inputs, *, max_rows_per_table=100):
    if len(inputs) > 16 or sum(len(content) for _, content in inputs) > 16_777_216:
        raise LaneError('TABLEAU_HYPER_INPUT_BUDGET', 'Select at most sixteen extracts within the total byte budget.')
    response = invoke_hyper({'operation': 'inspect', 'max_rows_per_table': max_rows_per_table,
        'files': [{'name': name, 'content_base64': base64.b64encode(content).decode('ascii')} for name, content in inputs]})
    if [row['name'] for row in response['files']] != [name for name, _ in inputs]:
        raise LaneError('TABLEAU_HYPER_INPUT_BINDING', 'The native worker returned a different input set.')
    return response['files'], response['evidence']
