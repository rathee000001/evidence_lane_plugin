"""Hash-bound native binaries from the shared Windows Studio installation.

Retains the original tool identity, acquisition hashes and fixed-argv calls.
No lookup installs tools, trusts a historical v3 pointer, or mutates PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .errors import LaneError
from .hashing import canonical_json_bytes
from .installation_layout import studio_installation
from .storage import reject_links

POINTER_SCHEMA = 'evidence-lane.shared-native-installation.v4'


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(path, root):
    reject_links(path, root)
    with path.open('rb') as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576:
        raise LaneError('NATIVE_METADATA_BUDGET', 'The installation metadata exceeds its read budget.')
    return json.loads(raw)


def configured_runtime_root():
    if os.name != 'nt':
        return None
    root = studio_installation().active_root
    return root if (root / 'toolchains/native-installation.v4.json').is_file() else None


def installed_toolchain_record(tool_id, *, runtime_root):
    root = Path(runtime_root).absolute()
    reject_links(root, Path(root.anchor))
    value = _json(root / 'toolchains/native-installation.v4.json', root)
    body = dict(value)
    expected = body.pop('receipt_sha256', None)
    if (value.get('schema') != POINTER_SCHEMA or value.get('status') != 'verified'
            or hashlib.sha256(canonical_json_bytes(body)).hexdigest() != expected
            or len(value.get('tools', [])) > 128):
        raise LaneError('NATIVE_INSTALLATION_INVALID', 'The shared native installation record failed validation.')
    rows = value['tools']
    if len({row['tool_id'] for row in rows}) != len(rows):
        raise LaneError('NATIVE_INSTALLATION_INVALID', 'Each installed tool identity must be unique.')
    return next((row for row in rows if row['tool_id'] == tool_id), None)


@dataclass(frozen=True)
class NativeToolResolution:
    tool_id: str
    executable: Path
    executable_sha256: str
    version: str
    license_receipt_sha256: str
    source: str = 'shared_studio_installation'


def resolve_native_tool(tool_id, *, runtime_root):
    root = Path(runtime_root).absolute()
    row = installed_toolchain_record(tool_id, runtime_root=root)
    if row is None or row.get('status') != 'verified':
        raise LaneError('NATIVE_TOOL_NOT_INSTALLED', 'The selected native tool is not installed.', details={'tool_id': tool_id})
    relative = Path(row['executable'])
    if relative.is_absolute() or relative.drive or '..' in relative.parts or ':' in str(relative):
        raise LaneError('NATIVE_TOOL_PATH_INVALID', 'The executable must remain inside the shared installation.')
    path = root / relative
    reject_links(path, root)
    if not path.is_file() or _sha(path) != row['executable_sha256']:
        raise LaneError('NATIVE_TOOL_HASH_MISMATCH', 'The native executable differs from its installation record.')
    return NativeToolResolution(tool_id, path, row['executable_sha256'], row['version'], row['license_receipt_sha256'])


def try_resolve_native_tool(tool_id):
    root = configured_runtime_root()
    if root is None:
        return None
    try:
        return resolve_native_tool(tool_id, runtime_root=root)
    except (LaneError, OSError, KeyError, ValueError):
        return None


class NativeInvocationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    tool_id: str = Field(min_length=1, max_length=96)
    arguments: list[str] = Field(max_length=128)
    input_bytes: bytes | None = Field(default=None, max_length=8_388_608)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_output_bytes: int = Field(default=8_000_000, ge=1024, le=64_000_000)
    host_profile: str


def run_native_tool(request, *, runtime_root):
    if request.host_profile not in {'CODEX_DESKTOP', 'CODEX_CLI', 'CODEX_VM'}:
        raise LaneError('NATIVE_HOST_PROFILE_INVALID', 'Select a supported configured host profile.')
    if any('\x00' in arg for arg in request.arguments) or sum(len(arg) for arg in request.arguments) > 65536:
        raise LaneError('NATIVE_ARGUMENT_BUDGET', 'The fixed adapter arguments exceed their budget.')
    resolution = resolve_native_tool(request.tool_id, runtime_root=runtime_root)
    buffers, overflow = {'stdout': bytearray(), 'stderr': bytearray()}, []
    # A finite temporary stdin file avoids a blocked writer thread on vendor tools.
    with tempfile.TemporaryFile() as input_stream:
        input_stream.write(request.input_bytes or b'')
        input_stream.seek(0)
        process = subprocess.Popen([str(resolution.executable), *request.arguments],
            cwd=runtime_root, stdin=input_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        def drain(name):
            stream = getattr(process, name)
            try:
                while block := stream.read(65536):
                    if len(buffers[name]) + len(block) > request.max_output_bytes:
                        overflow.append(name)
                        process.kill()
                        return
                    buffers[name].extend(block)
            finally:
                stream.close()

        threads = [threading.Thread(target=drain, args=(name,), daemon=True) for name in buffers]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise LaneError('NATIVE_TOOL_TIMEOUT', 'The owned native invocation exceeded its duration budget.') from None
        finally:
            for thread in threads:
                thread.join(timeout=5)
        if overflow or any(thread.is_alive() for thread in threads):
            raise LaneError('NATIVE_TOOL_OUTPUT_BOUND_EXCEEDED', 'The native invocation exceeded its output budget.')
    if _sha(resolution.executable) != resolution.executable_sha256:
        raise LaneError('NATIVE_TOOL_CHANGED_DURING_EXECUTION', 'The tool binary changed during this invocation.')
    body = {'schema': 'evidence-lane.native-tool-invocation.v4',
            'status': 'PASS' if process.returncode == 0 else 'FAIL',
            'tool_id': resolution.tool_id, 'version': resolution.version,
            'host_profile': request.host_profile, 'host_profile_basis': 'configured_not_attested',
            'arguments_sha256': hashlib.sha256(canonical_json_bytes(request.arguments)).hexdigest(),
            'argument_count': len(request.arguments), 'arguments_disclosed': False,
            'input_sha256': hashlib.sha256(request.input_bytes).hexdigest() if request.input_bytes is not None else None,
            'stdout': buffers['stdout'].decode('utf-8', errors='replace'),
            'stderr': buffers['stderr'].decode('utf-8', errors='replace'), 'returncode': process.returncode,
            'executable_sha256': resolution.executable_sha256, 'execution_owner': 'engine_adapter_child_process',
            'license_receipt_sha256': resolution.license_receipt_sha256, 'shell_used': False,
            'installed_dependency_scope': 'shared_studio_bundle', 'workspace_install_used': False}
    return {**body, 'receipt_sha256': hashlib.sha256(canonical_json_bytes(body)).hexdigest()}


def validate_dot_source(source, *, runtime_root, host_profile, timeout_seconds=30):
    return run_native_tool(NativeInvocationRequest(tool_id='graphviz', arguments=['-Tdot'],
        input_bytes=source.encode('utf-8'), host_profile=host_profile, timeout_seconds=timeout_seconds),
        runtime_root=runtime_root)


def validate_json_with_jq(source, *, runtime_root, host_profile):
    return run_native_tool(NativeInvocationRequest(tool_id='jq', arguments=['--sort-keys', '.'],
        input_bytes=source, host_profile=host_profile), runtime_root=runtime_root)
