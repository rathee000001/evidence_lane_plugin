"""Trusted engine bindings for separately installed optional provider runtimes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .accelerators import RuntimeEvidence
from .errors import LaneError

PROVIDERS = {"cuda": "NVIDIA_CUDA", "rocm": "AMD_ROCM", "directml": "DIRECTML"}


def read_bounded_json(path: Path, maximum=262_144):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise LaneError("RUNTIME_METADATA_TOO_LARGE", "The runtime metadata exceeds its limit.")
    return json.loads(raw)


@dataclass(frozen=True)
class OptionalRuntime:
    """Construct only from an engine-owned installation; never from action args."""

    runtime_id: str
    python: Path
    manifest_path: Path
    manifest_sha256: str
    lock_sha256: str

    @classmethod
    def from_installation(cls, directory: Path, contracts: Path) -> OptionalRuntime:
        directory = directory.resolve(strict=True)
        contracts = contracts.resolve(strict=True)
        record = read_bounded_json(directory / "environment.json")
        identity = record["runtime_id"]
        if identity not in {"cpu", *PROVIDERS}:
            raise LaneError("RUNTIME_IDENTITY_INVALID", "The runtime identifier is unsupported.")
        manifest = (contracts / f"{identity}.json").resolve(strict=True)
        executable = (directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).absolute()
        if (Path(record["python"]).absolute() != executable or not executable.is_file()
                or Path(record["manifest_path"]).resolve() != manifest
                or record["installation_state"] != "installed_from_locked_wheels"):
            raise LaneError("RUNTIME_BINDING_CHANGED", "The selected runtime installation does not match its record.")
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        contract = read_bounded_json(manifest)
        if (digest != record["manifest_sha256"] or contract["runtime_id"] != identity
                or contract["lock_sha256"] != record["lock_sha256"]):
            raise LaneError("RUNTIME_MANIFEST_CHANGED", "The selected runtime manifest changed.")
        lock = (contracts / contract['lock_file']).resolve(strict=True)
        if lock.parent != contracts or hashlib.sha256(lock.read_bytes()).hexdigest() != contract['lock_sha256']:
            raise LaneError('RUNTIME_LOCK_CHANGED', 'The installed runtime lock differs from its pinned identity.')
        return cls(identity, executable, manifest, digest, contract["lock_sha256"])

    def supports_operation(self, operation: str) -> bool:
        binding = self.from_installation(self.python.parent.parent, self.manifest_path.parent)
        if binding != self:
            raise LaneError('RUNTIME_BINDING_CHANGED', 'The engine-owned provider binding changed.')
        contract = read_bounded_json(self.manifest_path)
        # A self-test-only environment cannot advertise a real lane operation.
        required = {'code_embed_text': {'sentence-transformers', 'torch', 'numpy'},
            'rapidocr_lines': {'rapidocr', 'onnxruntime-directml', 'onnx', 'numpy', 'pillow'}}
        providers = {'code_embed_text': {'cpu', 'cuda', 'rocm'}, 'rapidocr_lines': {'directml'}}
        packages = {row['name'].lower().replace('_', '-') for row in contract['packages']}
        return (operation in contract.get('operations', []) and operation in required
                and required[operation] <= packages and self.runtime_id in providers[operation]
                and (operation != 'rapidocr_lines' or ('onnxruntime' not in packages
                    and len(packages & {'opencv-python', 'opencv-python-headless'}) == 1)))

    def worker_binding(self) -> dict:
        return {'runtime_id': self.runtime_id, 'python': str(self.python), 'manifest_path': str(self.manifest_path),
            'manifest_sha256': self.manifest_sha256, 'lock_sha256': self.lock_sha256}

    @classmethod
    def from_worker_binding(cls, value: dict) -> OptionalRuntime:
        selected = cls.from_installation(Path(value['python']).parent.parent, Path(value['manifest_path']).parent)
        if any(value.get(key) != expected for key, expected in selected.worker_binding().items()):
            raise LaneError('RUNTIME_BINDING_CHANGED', 'The owned worker received a stale provider installation binding.')
        return selected

    def execute(self, operation: str, arguments: dict, *, device_id: str, device_index: int,
                required_vram_mib: int, timeout: int = 120, server_binding: dict | None = None) -> dict:
        """A fixed isolated lane operation, called inside an owned OS worker.

        The parent worker's Windows lifetime group owns this child as well.
        Each attempted operation returns once; failures never trigger a replay.
        """
        if (not self.supports_operation(operation) or not 0 <= device_index <= 255
                or not 1 <= required_vram_mib <= 1_048_576 or not 1 <= timeout <= 300
                or not 1 <= len(device_id) <= 128):
            raise LaneError('PROVIDER_OPERATION_UNAVAILABLE', 'The selected installed runtime does not support this bounded lane operation.')
        if server_binding is not None:
            from .provider_client import call_provider_worker

            value = call_provider_worker(server_binding, operation, arguments, timeout=timeout)
            expected = {'selected_provider': PROVIDERS.get(self.runtime_id, 'CPU'), 'runtime_id': self.runtime_id,
                'device_id': device_id, 'device_index': device_index, 'environment_digest': self.lock_sha256,
                'runtime_manifest_sha256': self.manifest_sha256, 'execution_state': 'executed',
                'provider_worker_id': server_binding['worker_id']}
            if any(value.get('compute', {}).get(key) != item for key, item in expected.items()):
                raise LaneError('PROVIDER_RESULT_INVALID', 'The resident provider returned a different binding; no replay was issued.')
            return value
        nonce = str(uuid4())
        request = self.worker_binding() | {'operation': operation, 'arguments': arguments,
            'device_id': device_id, 'device_index': device_index, 'request_id': nonce,
            'required_vram_mib': required_vram_mib}
        payload = json.dumps(request, allow_nan=False).encode()
        if len(payload) > 1_048_576:
            raise LaneError('PROVIDER_INPUT_BUDGET', 'The provider input exceeds the worker byte budget.')
        environment = {key: value for key, value in os.environ.items() if key.upper() not in {
            'CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES', 'PYTHONPATH', 'PYTHONHOME'}}
        environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                completed = subprocess.run([str(self.python), '-I', str(Path(__file__).with_name('provider_operations.py'))],
                    input=payload, stdout=output, stderr=errors, env=environment, check=False, timeout=timeout,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (subprocess.TimeoutExpired, OSError):
                raise LaneError('PROVIDER_OPERATION_FAILED', 'The isolated provider did not return; no replay was issued.') from None
            output.seek(0)
            raw = output.read(4_194_305)
        try:
            if completed.returncode or len(raw) > 4_194_304:
                raise ValueError('Provider response failed')
            result = json.loads(raw)
            if result['request_id'] != nonce or result['operation'] != operation or result['status'] != 'ok':
                raise ValueError('Unbound provider response')
            evidence = result['result']['compute']
            expected = {'selected_provider': PROVIDERS.get(self.runtime_id, 'CPU'), 'runtime_id': self.runtime_id,
                'device_id': device_id, 'device_index': device_index, 'environment_digest': self.lock_sha256,
                'runtime_manifest_sha256': self.manifest_sha256, 'execution_state': 'executed'}
            if any(evidence.get(key) != value for key, value in expected.items()):
                raise ValueError('Provider result changed its selected environment or device')
            return result['result']
        except (ValueError, KeyError, TypeError):
            raise LaneError('PROVIDER_RESULT_INVALID', 'The isolated provider did not return a result bound to this operation; no replay was issued.') from None

    def probe(self, *, device_id: str = "cpu", device_index: int = 0, timeout: int = 120) -> dict:
        if not 0 <= device_index <= 255 or not 1 <= timeout <= 300 or len(device_id) > 128:
            raise ValueError("Probe arguments exceed their bounds")
        nonce = str(uuid4())
        request = {"runtime_id": self.runtime_id, "manifest_path": str(self.manifest_path),
                   "manifest_sha256": self.manifest_sha256, "device_id": device_id,
                   "device_index": device_index, "probe_id": nonce}
        # Do not allow caller environment masks to remap an indexed GPU. Python
        # isolated mode also excludes user-site, PYTHONPATH and cwd imports.
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() not in {"CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"}}
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                completed = subprocess.run(
                    [str(self.python), "-I", str(Path(__file__).with_name("provider_probe.py"))],
                    input=json.dumps(request).encode(), stdout=output, stderr=errors,
                    env=environment, timeout=timeout, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired:
                return {"runtime_id": self.runtime_id, "self_test": "unavailable", "reason": "PROVIDER_PROBE_TIMEOUT", "probe_id": nonce}
            except OSError:
                return {"runtime_id": self.runtime_id, "self_test": "unavailable", "reason": "PROVIDER_INTERPRETER_UNAVAILABLE", "probe_id": nonce}
            output.seek(0)
            raw = output.read(65_537)
        if completed.returncode or len(raw) > 65_536:
            return {"runtime_id": self.runtime_id, "self_test": "failed", "reason": "PROVIDER_PROCESS_FAILED", "probe_id": nonce}
        try:
            result = json.loads(raw)
            if (result["probe_id"] != nonce or result["runtime_id"] != self.runtime_id
                    or result["self_test"] not in {"passed", "failed", "unavailable"}):
                raise ValueError("Unbound probe")
            if result["self_test"] == "passed" and (
                    result["environment_digest"] != self.lock_sha256 or result["device_id"] != device_id):
                raise ValueError("Unbound runtime result")
        except (KeyError, ValueError, TypeError):
            return {"runtime_id": self.runtime_id, "self_test": "failed", "reason": "PROVIDER_RESULT_INVALID", "probe_id": nonce}
        return result

    def accelerator_evidence(self, result: dict) -> RuntimeEvidence | None:
        if self.runtime_id == "cpu" or result.get("self_test") != "passed":
            return None
        if result.get("runtime_id") != self.runtime_id or result.get("environment_digest") != self.lock_sha256:
            raise LaneError("RUNTIME_EVIDENCE_MISMATCH", "The result does not belong to this runtime.")
        return RuntimeEvidence(provider=PROVIDERS[self.runtime_id], device_id=result["device_id"],
                               device_index=result.get('device_index'),
                               runtime_version=result["runtime_version"],
                               runtime_platform_version=result["runtime_platform_version"],
                               environment_digest=self.lock_sha256, observed_at=result["observed_at"],
                               self_test="passed", probe_id=result["probe_id"], reason=result["reason"])
