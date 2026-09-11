"""Plugin-owned engine discovery and launch, shared with Studio and native MCP."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .build import runtime_source_identity
from .errors import LaneError
from .locking import RuntimeLock
from .storage import reject_links


def runtime_root(selected: Path | None = None) -> Path:
    """Use an explicit selection or the one per-user v4 runtime location."""
    if selected is None:
        configured = os.environ.get('EVIDENCE_LANE_RUNTIME_ROOT')
        if configured is not None:
            if not configured.strip():
                raise LaneError('RUNTIME_PATH_INVALID', 'A configured runtime path cannot be empty.')
            selected = Path(configured)
        elif os.name == 'nt':
            from .installation_layout import studio_installation
            selected = studio_installation().engine_runtime
        elif platform.system() == 'Darwin':
            selected = Path.home() / 'Library/Application Support/EvidenceLane/runtime'
        elif platform.system() == 'Linux':
            selected = Path.home() / '.local/share/EvidenceLane/runtime'
        else:
            raise LaneError('LOCAL_RUNTIME_UNSUPPORTED', 'Select a supported local runtime or an explicit remote engine.')
    if not selected.is_absolute() or any(value in str(selected) for value in '\r\n\x00'):
        raise LaneError('RUNTIME_PATH_INVALID', 'Select an absolute runtime directory.')
    selected = Path(os.path.abspath(selected))
    reject_links(selected, Path(selected.anchor))
    return selected


def verify_engine_binding(transport, expected: dict | None = None) -> dict:
    from .sdk import EvidenceLaneClient
    expected = expected or runtime_source_identity()
    response = EvidenceLaneClient(transport).call('engine_health')
    health = response.result if response.status == 'ok' else None
    identity = health.get('runtime_identity', {}) if isinstance(health, dict) else {}
    if (not health or health.get('version') != __version__
            or identity.get('source_digest') != expected['source_digest']
            or identity.get('module_root') != expected['module_root']):
        raise LaneError('ENGINE_BUILD_MISMATCH',
            'The selected engine does not match this plugin executable. Quiesce and replace it through the plugin update flow.')
    if health.get('phase') != 'running':
        raise LaneError('ENGINE_NOT_RUNNING', 'The selected engine is draining or stopped.')
    return health


def _probe(root: Path, expected: dict) -> dict:
    from .local_transport import LocalTransport
    with LocalTransport(root, timeout=2) as transport:
        return verify_engine_binding(transport, expected)


def _service_command(root: Path) -> list[str]:
    interpreter = Path(sys.executable).resolve()
    if os.name == 'nt' and interpreter.with_name('pythonw.exe').is_file():
        interpreter = interpreter.with_name('pythonw.exe')
    package = Path(__file__).resolve().parents[2]
    script = package / 'scripts/run_engine.py'
    if script.is_file() and (package / '.codex-plugin/plugin.json').is_file():
        reject_links(script, package)
        return [str(interpreter), '-I', '-B', str(script), '--runtime-root', str(root)]
    return [str(interpreter), '-I', '-m', 'evidence_lane_plugin.service', '--runtime-root', str(root)]


def ensure_local_engine(root: Path, *, timeout: float = 30, spawn=None) -> dict:
    """Start at most one owned service; never replace or kill a live engine.

    Dependency provisioning belongs to the installed plugin bootstrap. A
    development launch uses its already provisioned interpreter.
    """
    root = runtime_root(root)
    expected = runtime_source_identity()
    deadline = time.monotonic() + timeout
    process = None
    launch = RuntimeLock(root / 'launcher.lock')
    while True:
        try:
            return _probe(root, expected)
        except LaneError as error:
            if error.code not in {'ENGINE_UNAVAILABLE', 'LOCAL_TRANSPORT_FAILED'}:
                raise
            # A malformed existing discovery document must not be overwritten.
            if error.code == 'ENGINE_UNAVAILABLE' and (root / 'endpoint.json').exists():
                raise LaneError('RUNTIME_DISCOVERY_INVALID', 'The existing engine discovery file could not be verified.') from None
        try:
            launch.acquire()
            break
        except LaneError as error:
            if error.code != 'RUNTIME_IN_USE':
                raise
        if time.monotonic() >= deadline:
            raise LaneError('ENGINE_LAUNCH_TIMEOUT', 'Another plugin connection has not finished starting the engine.')
        time.sleep(0.1)
    try:
        # A previous launcher may have completed while we acquired its lock.
        try:
            return _probe(root, expected)
        except LaneError as error:
            if error.code not in {'ENGINE_UNAVAILABLE', 'LOCAL_TRANSPORT_FAILED'}:
                raise
        ownership = RuntimeLock(root / 'engine.lock')
        try:
            ownership.acquire()
        except LaneError as error:
            if error.code != 'RUNTIME_IN_USE':
                raise
        else:
            ownership.release()
            # Explicit argv; no shell, visible console, user restart helper or
            # authority mutation. The service owns its normal Studio window.
            process = (spawn or subprocess.Popen)(_service_command(root), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while time.monotonic() < deadline:
            try:
                return _probe(root, expected)
            except LaneError as error:
                if error.code not in {'ENGINE_UNAVAILABLE', 'LOCAL_TRANSPORT_FAILED'}:
                    raise
            if process is not None and process.poll() is not None:
                raise LaneError('ENGINE_START_FAILED', 'The plugin engine exited before its connection was ready; inspect runtime readiness.')
            time.sleep(0.1)
        raise LaneError('ENGINE_LAUNCH_TIMEOUT', 'The plugin engine did not become ready within the launch budget.')
    finally:
        launch.release()
