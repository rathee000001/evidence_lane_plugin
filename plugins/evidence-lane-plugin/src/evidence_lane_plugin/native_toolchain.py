"""Hash-bound native tool resolution and bounded invocation for Codex hosts."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - exact verified executable, fixed argv, no shell
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file

NATIVE_MANIFEST_SCHEMA = "evidence-lane.native-toolchain-manifest.v1"
NATIVE_POINTER_SCHEMA = "evidence-lane.installed-native-toolchain.v1"
RUNTIME_ROOT_ENV = "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"


class NativeInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    arguments: list[str]
    input_bytes: bytes | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_output_bytes: int = Field(default=8_000_000, ge=1_024, le=64_000_000)
    host_profile: str


@dataclass(frozen=True, slots=True)
class NativeToolResolution:
    tool_id: str
    executable: Path
    executable_sha256: str
    version: str
    license_receipt_sha256: str
    source: str


def plugin_root() -> Path:
    for ancestor in Path(__file__).resolve().parents:
        if (
            (ancestor / ".codex-plugin" / "plugin.json").is_file()
            and (ancestor / "toolchains" / "native-tools.v1.json").is_file()
        ):
            return ancestor
    raise RuntimeError("NATIVE_TOOLCHAIN_PLUGIN_ROOT_UNAVAILABLE")


def native_manifest(root: Path | None = None) -> dict[str, Any]:
    exact_root = (root or plugin_root()).resolve()
    path = exact_root / "toolchains" / "native-tools.v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema") != NATIVE_MANIFEST_SCHEMA
        or value.get("plane") != "CODEX"
        or value.get("workspace_install_allowed") is not False
        or value.get("path_mutation_allowed") is not False
        or value.get("acquisition_gate") != "LOCAL_UPDATE_ONLY"
    ):
        raise RuntimeError("NATIVE_TOOLCHAIN_MANIFEST_INVALID")
    return value


def validate_hidden_runtime_root(runtime_root: str | Path) -> Path:
    root = Path(runtime_root).resolve()
    folded = [part.casefold() for part in root.parts]
    required = [".codex", "plugins", "runtime", "evidence-lane-plugin"]
    cursor = 0
    for part in folded:
        if cursor < len(required) and part == required[cursor]:
            cursor += 1
    if cursor != len(required):
        raise ValueError("NATIVE_TOOLCHAIN_HIDDEN_RUNTIME_ROOT_REQUIRED")
    return root


def configured_runtime_root() -> Path | None:
    value = os.environ.get(RUNTIME_ROOT_ENV, "").strip()
    return validate_hidden_runtime_root(value) if value else None


def _pointer(runtime_root: Path) -> dict[str, Any]:
    path = runtime_root / "toolchains" / "CURRENT_NATIVE_TOOLCHAIN.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != NATIVE_POINTER_SCHEMA or value.get("status") != "PASS":
        raise RuntimeError("INSTALLED_NATIVE_TOOLCHAIN_POINTER_INVALID")
    body = dict(value)
    expected = str(body.pop("receipt_sha256", ""))
    if sha256_bytes(canonical_json_bytes(body)) != expected:
        raise RuntimeError("INSTALLED_NATIVE_TOOLCHAIN_POINTER_SELF_SEAL_INVALID")
    return value


def installed_toolchain_record(
    tool_id: str,
    *,
    runtime_root: str | Path,
) -> dict[str, Any] | None:
    root = validate_hidden_runtime_root(runtime_root)
    pointer = _pointer(root)
    return next(
        (
            dict(row)
            for row in pointer.get("tools") or []
            if str(row.get("tool_id") or "") == tool_id
        ),
        None,
    )


def resolve_native_tool(
    tool_id: str,
    *,
    runtime_root: str | Path,
) -> NativeToolResolution:
    root = validate_hidden_runtime_root(runtime_root)
    exact_id = tool_id.strip().lower()
    if exact_id == "ffmpeg":
        import imageio_ffmpeg  # type: ignore[import-not-found]

        executable = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve(strict=True)
        return NativeToolResolution(
            "ffmpeg",
            executable,
            sha256_file(executable),
            "imageio-ffmpeg==0.6.0",
            "PYTHON_WHEEL_METADATA",
            "HIDDEN_RUNTIME_PYTHON_WHEEL",
        )
    pointer = _pointer(root)
    rows = {
        str(row["tool_id"]): dict(row) for row in pointer.get("tools") or []
    }
    row = rows.get(exact_id)
    if row is None or row.get("status") != "PASS":
        raise RuntimeError(f"NATIVE_TOOL_NOT_INSTALLED:{exact_id}")
    executable = Path(str(row["executable"])).resolve(strict=True)
    try:
        executable.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("NATIVE_TOOL_OUTSIDE_HIDDEN_RUNTIME") from exc
    observed = sha256_file(executable)
    if observed != str(row["executable_sha256"]):
        raise RuntimeError("NATIVE_TOOL_EXECUTABLE_HASH_MISMATCH")
    return NativeToolResolution(
        exact_id,
        executable,
        observed,
        str(row["version"]),
        str(row["license_receipt_sha256"]),
        "INSTALLED_HIDDEN_RUNTIME_POINTER",
    )


def try_resolve_native_tool(tool_id: str) -> NativeToolResolution | None:
    root = configured_runtime_root()
    if root is None:
        return None
    try:
        return resolve_native_tool(tool_id, runtime_root=root)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return None


def run_native_tool(
    request: NativeInvocationRequest,
    *,
    runtime_root: str | Path,
) -> dict[str, Any]:
    host = request.host_profile.strip().upper()
    if host not in {"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"}:
        raise ValueError("NATIVE_TOOL_CODEX_HOST_PROFILE_REQUIRED")
    resolution = resolve_native_tool(request.tool_id, runtime_root=runtime_root)
    completed = subprocess.run(  # nosec B603
        [str(resolution.executable), *request.arguments],
        input=request.input_bytes,
        capture_output=True,
        check=False,
        timeout=request.timeout_seconds,
        shell=False,
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
    )
    if (
        len(completed.stdout) > request.max_output_bytes
        or len(completed.stderr) > request.max_output_bytes
    ):
        raise RuntimeError("NATIVE_TOOL_OUTPUT_BOUND_EXCEEDED")
    core = {
        "schema": "evidence-lane.native-tool-invocation.v1",
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "tool_id": resolution.tool_id,
        "version": resolution.version,
        "host_profile": host,
        "argument_count": len(request.arguments),
        "arguments_sha256": sha256_bytes(
            canonical_json_bytes(request.arguments)
        ),
        "arguments_disclosed": False,
        "input_sha256": (
            sha256_bytes(request.input_bytes) if request.input_bytes is not None else None
        ),
        "stdout": completed.stdout.decode("utf-8", errors="replace"),
        "stderr": completed.stderr.decode("utf-8", errors="replace"),
        "returncode": int(completed.returncode),
        "executable_sha256": resolution.executable_sha256,
        "executable_path_disclosed": False,
        "license_receipt_sha256": resolution.license_receipt_sha256,
        "shell_used": False,
        "workspace_install_used": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def validate_dot_source(
    source: str,
    *,
    runtime_root: str | Path,
    host_profile: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    return run_native_tool(
        NativeInvocationRequest(
            tool_id="graphviz",
            arguments=["-Tdot"],
            input_bytes=source.encode("utf-8"),
            host_profile=host_profile,
            timeout_seconds=timeout_seconds,
        ),
        runtime_root=runtime_root,
    )


def validate_json_with_jq(
    source: bytes,
    *,
    runtime_root: str | Path,
    host_profile: str,
) -> dict[str, Any]:
    return run_native_tool(
        NativeInvocationRequest(
            tool_id="jq",
            arguments=["--sort-keys", "."],
            input_bytes=source,
            host_profile=host_profile,
        ),
        runtime_root=runtime_root,
    )


__all__ = [
    "NATIVE_MANIFEST_SCHEMA",
    "NATIVE_POINTER_SCHEMA",
    "RUNTIME_ROOT_ENV",
    "NativeInvocationRequest",
    "NativeToolResolution",
    "configured_runtime_root",
    "installed_toolchain_record",
    "native_manifest",
    "plugin_root",
    "resolve_native_tool",
    "run_native_tool",
    "try_resolve_native_tool",
    "validate_dot_source",
    "validate_hidden_runtime_root",
    "validate_json_with_jq",
]
