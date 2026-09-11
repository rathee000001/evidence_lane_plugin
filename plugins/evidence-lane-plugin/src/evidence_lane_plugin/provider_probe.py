"""Standalone Python 3.12+ probe, invoked with the locked interpreter and -I.

No engine imports or dynamic operation names. A listed provider is not proof of
execution; only an actual, checked result qualifies the requested device.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


class ProbeUnavailable(Exception):
    pass


def enumerate_dxgi() -> list[dict]:
    """Read actual DXGI adapter order and PCI vendor; LUID is boot-local.

    DXGI does not supply global usage, temperature or throttling here. Those
    measurements remain unavailable instead of being replaced by estimates.
    """
    if os.name != "nt":
        return []

    class Luid(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]

    class Description(ctypes.Structure):
        _fields_ = [("name", ctypes.c_wchar * 128), ("vendor", ctypes.c_uint32),
                    ("device", ctypes.c_uint32), ("subsystem", ctypes.c_uint32),
                    ("revision", ctypes.c_uint32), ("dedicated", ctypes.c_size_t),
                    ("dedicated_system", ctypes.c_size_t), ("shared", ctypes.c_size_t),
                    ("luid", Luid)]

    def method(pointer, slot, *types):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, *types)(table[slot])

    factory = ctypes.c_void_p()
    guid = (ctypes.c_byte * 16).from_buffer_copy(UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    dxgi = ctypes.WinDLL("dxgi.dll", winmode=0x800)  # System32 only.
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_int32
    if create(ctypes.byref(guid), ctypes.byref(factory)) < 0:
        raise ProbeUnavailable("DXGI_FACTORY_UNAVAILABLE")
    adapters = []
    try:
        # IDXGIFactory::EnumAdapters slot 7; same ordering used by DirectML EP.
        for index in range(32):
            adapter = ctypes.c_void_p()
            result = method(factory, 7, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(
                factory, index, ctypes.byref(adapter))
            if result & 0xFFFFFFFF == 0x887A0002:  # DXGI_ERROR_NOT_FOUND
                break
            if result < 0:
                raise ProbeUnavailable("DXGI_ENUMERATION_FAILED")
            try:
                description = Description()
                if method(adapter, 8, ctypes.POINTER(Description))(adapter, ctypes.byref(description)) < 0:
                    raise ProbeUnavailable("DXGI_DESCRIPTION_FAILED")
                adapters.append({"device_index": index, "vendor_id": description.vendor,
                                 "device_id": f"DXGI-{description.luid.high & 0xFFFFFFFF:08x}{description.luid.low:08x}",
                                 "name": description.name, "pci_device_id": description.device,
                                 "total_vram_mib": description.dedicated // (1024 * 1024) or None})
            finally:
                method(adapter, 2)(adapter)
    finally:
        method(factory, 2)(factory)
    return adapters


def nvidia_dxgi_adapter(device_id: str, adapters: list[dict]) -> dict:
    """Map an NVIDIA GPU UUID to its exact Windows DXGI LUID through CUDA."""

    if os.name != "nt" or not device_id.startswith("GPU-"):
        raise ProbeUnavailable("DIRECTML_DEVICE_UNAVAILABLE")
    try:
        requested = UUID(device_id.removeprefix("GPU-"))
        cuda = ctypes.WinDLL("nvcuda.dll", winmode=0x800)
        cuda.cuInit.argtypes = [ctypes.c_uint]
        cuda.cuInit.restype = ctypes.c_int
        cuda.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        cuda.cuDeviceGetCount.restype = ctypes.c_int
        uuid_call = getattr(cuda, "cuDeviceGetUuid_v2", cuda.cuDeviceGetUuid)
        uuid_call.argtypes = [ctypes.c_void_p, ctypes.c_int]
        uuid_call.restype = ctypes.c_int
        cuda.cuDeviceGetLuid.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.c_int,
        ]
        cuda.cuDeviceGetLuid.restype = ctypes.c_int
        count = ctypes.c_int()
        if cuda.cuInit(0) != 0 or cuda.cuDeviceGetCount(ctypes.byref(count)) != 0:
            raise ProbeUnavailable("DIRECTML_DEVICE_UNAVAILABLE")
        if not 0 <= count.value <= 32:
            raise ProbeUnavailable("PROVIDER_DEVICE_BUDGET")
        matches: list[dict] = []
        for index in range(count.value):
            raw_uuid = (ctypes.c_ubyte * 16)()
            if uuid_call(ctypes.byref(raw_uuid), index) != 0:
                continue
            if UUID(bytes=bytes(raw_uuid)) != requested:
                continue
            raw_luid = (ctypes.c_ubyte * 8)()
            node_mask = ctypes.c_uint()
            if (
                cuda.cuDeviceGetLuid(
                    ctypes.byref(raw_luid), ctypes.byref(node_mask), index
                )
                != 0
                or node_mask.value == 0
            ):
                continue
            luid = f"DXGI-{int.from_bytes(bytes(raw_luid), 'little'):016x}"
            matches.extend(
                row
                for row in adapters
                if row["vendor_id"] == 0x10DE and row["device_id"] == luid
            )
        if len(matches) != 1:
            raise ProbeUnavailable("PROVIDER_DEVICE_IDENTITY_MISMATCH")
        return matches[0]
    except (OSError, ValueError):
        raise ProbeUnavailable("DIRECTML_DEVICE_UNAVAILABLE") from None


def directml_adapter(request: dict) -> dict:
    """Resolve one supported NVIDIA or AMD device to its exact DXGI adapter."""

    adapters = enumerate_dxgi()
    device_id = request["device_id"]
    selected: dict | None
    if device_id.startswith("GPU-"):
        selected = nvidia_dxgi_adapter(device_id, adapters)
    else:
        selected = next(
            (
                item
                for item in adapters
                if item["device_index"] == request["device_index"]
            ),
            None,
        )
        if selected is not None and selected["device_id"] != device_id:
            raise ProbeUnavailable("PROVIDER_DEVICE_IDENTITY_MISMATCH")
    if selected is None or selected["vendor_id"] not in {0x1002, 0x10DE}:
        raise ProbeUnavailable("DIRECTML_DEVICE_UNAVAILABLE")
    return selected


def verify_environment(manifest_path: Path, expected_digest: str) -> dict:
    raw = manifest_path.read_bytes()
    if len(raw) > 262_144 or hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ProbeUnavailable("RUNTIME_MANIFEST_CHANGED")
    manifest = json.loads(raw)
    lock = manifest_path.parent / manifest["lock_file"]
    if lock.resolve().parent != manifest_path.resolve().parent:
        raise ProbeUnavailable("RUNTIME_LOCK_PATH_INVALID")
    if hashlib.sha256(lock.read_bytes()).hexdigest() != manifest["lock_sha256"]:
        raise ProbeUnavailable("RUNTIME_LOCK_CHANGED")
    if (f"{sys.version_info.major}.{sys.version_info.minor}" != manifest["python_version"]
            or platform.system() != manifest["system"] or platform.machine() != manifest["machine"]):
        raise ProbeUnavailable("RUNTIME_PLATFORM_MISMATCH")
    if sys.prefix == sys.base_prefix:
        raise ProbeUnavailable("ISOLATED_ENVIRONMENT_REQUIRED")
    prefix = Path(sys.prefix).resolve()
    for package in manifest["packages"]:
        try:
            distribution = importlib.metadata.distribution(package["name"])
        except importlib.metadata.PackageNotFoundError:
            raise ProbeUnavailable("LOCKED_PACKAGE_MISSING") from None
        if distribution.version != package["version"]:
            raise ProbeUnavailable("LOCKED_PACKAGE_VERSION_CHANGED")
        # RECORD hashes are installed-file integrity evidence, separate from
        # the original publisher wheel hashes used by the installer.
        for entry in distribution.files or []:
            path = Path(str(distribution.locate_file(entry))).resolve()
            if not path.is_relative_to(prefix):
                raise ProbeUnavailable("PACKAGE_OUTSIDE_ISOLATED_ENVIRONMENT")
            if entry.hash is None:
                continue  # RECORD and generated pyc entries are unhashed.
            if entry.hash.mode != "sha256" or not path.is_file():
                raise ProbeUnavailable("PACKAGE_FILE_INTEGRITY_UNAVAILABLE")
            with path.open("rb") as stream:
                actual = base64.urlsafe_b64encode(hashlib.file_digest(stream, "sha256").digest()).rstrip(b"=").decode()
            if actual != entry.hash.value:
                raise ProbeUnavailable("PACKAGE_FILE_CHANGED")
    return manifest


def rocm_windows_index(torch, request):
    """Join HIP R0600 LUID to DXGI; HIP and DXGI index order may differ.

    The pinned ROCm 7 SDK exposes LUID in hipDeviceProp_tR0600 (name[256],
    UUID[16], LUID[8]); hipDeviceGetLuid is not exported by this older pin.
    The oversize aligned buffer safely accommodates the complete fixed ABI.
    """
    expected = next((row for row in enumerate_dxgi() if row['device_id'] == request['device_id']
                     and row['vendor_id'] == 0x1002), None)
    if expected is None:
        raise ProbeUnavailable('AMD_ROCM_DEVICE_UNAVAILABLE')
    distribution = importlib.metadata.distribution('rocm-sdk-core')
    dlls = [Path(distribution.locate_file(row)).resolve() for row in distribution.files
            if str(row).replace('\\', '/').endswith('/bin/amdhip64_7.dll')]
    if len(dlls) != 1 or not dlls[0].is_relative_to(Path(sys.prefix).resolve()):
        raise ProbeUnavailable('ROCM_DEVICE_BINDING_UNAVAILABLE')
    library = ctypes.CDLL(str(dlls[0]), winmode=0x1100)
    properties = library.hipGetDevicePropertiesR0600
    properties.argtypes, properties.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_int
    count = torch.cuda.device_count()
    if not 0 <= count <= 32:
        raise ProbeUnavailable('PROVIDER_DEVICE_BUDGET')
    matches = []
    for index in range(count):
        buffer = (ctypes.c_uint64 * 8192)()
        if properties(ctypes.byref(buffer), index) != 0:
            continue
        raw = bytes(buffer)
        identity = f"DXGI-{int.from_bytes(raw[272:280], 'little'):016x}"
        if identity == request['device_id']:
            matches.append(index)
    if len(matches) != 1 or ('operation' in request and matches[0] != request['device_index']):
        raise ProbeUnavailable('PROVIDER_DEVICE_IDENTITY_MISMATCH')
    return matches[0]


def torch_probe(runtime: str, request: dict) -> dict:
    if runtime == "rocm" and os.name == "nt" and not any(item["vendor_id"] == 0x1002 for item in enumerate_dxgi()):
        raise ProbeUnavailable("AMD_ROCM_DEVICE_UNAVAILABLE")
    import torch

    torch.set_num_threads(1)
    if runtime == "cpu":
        if torch.version.cuda or torch.version.hip:
            raise ProbeUnavailable("CPU_RUNTIME_BUILD_MISMATCH")
        device = "cpu"
        identity = "cpu"
        name = platform.processor() or "CPU"
        platform_version = None
    else:
        if runtime == "cuda" and (not torch.version.cuda or torch.version.hip):
            raise ProbeUnavailable("CUDA_RUNTIME_BUILD_MISMATCH")
        if runtime == "rocm" and not torch.version.hip:
            raise ProbeUnavailable("ROCM_RUNTIME_BUILD_MISMATCH")
        if not torch.cuda.is_available():
            raise ProbeUnavailable("PROVIDER_DEVICE_UNAVAILABLE")
        index = rocm_windows_index(torch, request) if runtime == 'rocm' and os.name == 'nt' else request["device_index"]
        if index >= torch.cuda.device_count():
            raise ProbeUnavailable("PROVIDER_DEVICE_INDEX_UNAVAILABLE")
        properties = torch.cuda.get_device_properties(index)
        identity = request['device_id'] if runtime == 'rocm' and os.name == 'nt' else str(getattr(properties, "uuid", ""))
        if runtime == "cuda" and identity and not identity.startswith("GPU-"):
            identity = "GPU-" + identity
        if not identity or identity != request["device_id"]:
            raise ProbeUnavailable("PROVIDER_DEVICE_IDENTITY_MISMATCH")
        name = properties.name
        platform_version = torch.version.cuda if runtime == "cuda" else torch.version.hip
        device = f"cuda:{index}"
    with torch.inference_mode():
        values = torch.arange(64, dtype=torch.float32, device=device).reshape(8, 8)
        result = (values @ torch.eye(8, device=device)) + 1
        if runtime != "cpu":
            torch.cuda.synchronize(device)
        output = result.cpu()
        expected = torch.arange(64, dtype=torch.float32).reshape(8, 8) + 1
        if not torch.equal(output, expected):
            raise ProbeUnavailable("PROVIDER_NUMERICAL_CHECK_FAILED")
    return {"device_id": identity, "device_name": name, "runtime_version": torch.__version__,
            "device_index": index if runtime != 'cpu' else 0,
            "runtime_platform_version": platform_version, "operation": "float32_matmul_add_8x8",
            "result_sha256": hashlib.sha256(output.numpy().tobytes()).hexdigest()}


def directml_probe(request: dict) -> dict:
    selected = directml_adapter(request)
    import numpy as np
    import onnx
    import onnxruntime as ort

    if "DmlExecutionProvider" not in ort.get_available_providers():
        raise ProbeUnavailable("DIRECTML_PROVIDER_UNAVAILABLE")
    tensor = onnx.TensorProto.FLOAT
    graph = onnx.helper.make_graph([onnx.helper.make_node("Add", ["a", "b"], ["out"])], "probe",
                                  [onnx.helper.make_tensor_value_info(name, tensor, [2, 2]) for name in ("a", "b")],
                                  [onnx.helper.make_tensor_value_info("out", tensor, [2, 2])])
    model = onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid("", 13)], ir_version=8)
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    options.enable_profiling = True
    with tempfile.TemporaryDirectory(prefix="evidence-lane-dml-") as temporary:
        options.profile_file_prefix = str(Path(temporary) / "probe")
        session = ort.InferenceSession(model.SerializeToString(), options,
                                      providers=[("DmlExecutionProvider", {"device_id": str(request["device_index"])})])
        session.disable_fallback()
        values = np.arange(4, dtype=np.float32).reshape(2, 2)
        output = session.run(["out"], {"a": values, "b": values})[0]
        profile = Path(session.end_profiling())
        if not profile.resolve().is_relative_to(Path(temporary).resolve()) or profile.stat().st_size > 1_048_576:
            raise ProbeUnavailable("DIRECTML_PROFILE_INVALID")
        events = json.loads(profile.read_text(encoding="utf-8"))
        providers = {item.get("args", {}).get("provider") for item in events if item.get("cat") == "Node"}
        if "DmlExecutionProvider" not in providers or "CPUExecutionProvider" in providers:
            raise ProbeUnavailable("DIRECTML_EXECUTION_UNPROVEN")
        if not np.array_equal(output, values * 2):
            raise ProbeUnavailable("PROVIDER_NUMERICAL_CHECK_FAILED")
    return {"device_id": request["device_id"], "dxgi_device_id": selected["device_id"],
            "device_name": selected["name"],
            "device_index": selected['device_index'],
            "runtime_version": ort.__version__, "runtime_platform_version": None,
            "operation": "onnx_float32_add_2x2", "result_sha256": hashlib.sha256(output.tobytes()).hexdigest()}


def main() -> None:
    request = {}
    verified_manifest = None
    try:
        raw = sys.stdin.buffer.read(65_537)
        if len(raw) > 65_536:
            raise ProbeUnavailable("PROBE_REQUEST_TOO_LARGE")
        request = json.loads(raw)
        manifest = verify_environment(Path(request["manifest_path"]), request["manifest_sha256"])
        verified_manifest = manifest
        runtime = manifest["runtime_id"]
        if runtime != request["runtime_id"] or runtime not in {"cpu", "cuda", "rocm", "directml"}:
            raise ProbeUnavailable("RUNTIME_IDENTITY_MISMATCH")
        result = directml_probe(request) if runtime == "directml" else torch_probe(runtime, request)
        result.update({"self_test": "passed", "reason": "MEASURED_OPERATION_PASSED",
                       "environment_digest": manifest["lock_sha256"], "installed_record_integrity": "verified"})
    except ProbeUnavailable as error:
        result = {"self_test": "unavailable", "reason": str(error)}
    except Exception:  # noqa: BLE001 - native provider failures must not echo request contents.
        result = {"self_test": "failed", "reason": "PROVIDER_PROBE_FAILED"}
    result.update({"probe_id": request.get("probe_id"), "runtime_id": request.get("runtime_id"),
                   'installed_record_integrity': 'verified' if verified_manifest is not None else 'unavailable',
                   'environment_digest': verified_manifest['lock_sha256'] if verified_manifest is not None else None,
                   "observed_at": datetime.now(UTC).isoformat()})
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
