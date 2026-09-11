"""Execute a pinned shared Power BI child under Windows process ownership."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading

from .errors import LaneError
from .hashing import canonical_json_bytes
from .process_ownership import ChildProcessGroup, ExtendedLimits, kernel
from .shared_tool_assets import resolve_shared_asset


def invoke_shared(asset_id, executable_name, request, *, script_name=None, timeout_seconds=45):
    if os.name != "nt":
        raise LaneError(
            "POWERBI_NATIVE_HOST_UNAVAILABLE",
            "Use the supported Windows backend for this bundled Power BI reader.",
        )
    folder, asset = resolve_shared_asset(asset_id)
    names = {row["path"] for row in asset["files"]}
    if executable_name not in names or script_name is not None and script_name not in names:
        raise LaneError(
            "POWERBI_RUNTIME_INVALID",
            "A required Power BI executable or adapter is absent from its verified asset.",
        )
    command = [str(folder / executable_name)]
    if script_name:
        command += ["-I", "-S", "-B", str(folder / script_name)]
    body = canonical_json_bytes(request)
    if len(body) > 25_165_824:
        raise LaneError("POWERBI_INPUT_BUDGET", "Select a smaller encoded Power BI model input.")
    group, process = ChildProcessGroup(), None
    group.start()
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000 | 0x100 | 0x8
        limits.basic.active_process_limit = 1
        limits.process_memory = 1_073_741_824
        if not kernel().SetInformationJobObject(
            group.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise LaneError(
                "POWERBI_OWNER_UNAVAILABLE", "The native reader requires its bounded owned process."
            )
        process = subprocess.Popen(
            command,
            cwd=folder,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not kernel().AssignProcessToJobObject(group.handle, int(process._handle)):
            raise LaneError(
                "POWERBI_OWNER_UNAVAILABLE",
                "The native reader could not join its owned process group.",
            )
        output, overflow = {"stdout": bytearray(), "stderr": bytearray()}, []

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
                process.stdin.write(body)
            except (OSError, ValueError):
                pass
            finally:
                process.stdin.close()

        threads = [
            threading.Thread(target=drain, args=("stdout", 33_554_432), daemon=True),
            threading.Thread(target=drain, args=("stderr", 4096), daemon=True),
            threading.Thread(target=send, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            raise LaneError(
                "POWERBI_PARSE_TIMEOUT", "The owned Power BI reader exceeded its duration budget."
            ) from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
        if overflow or any(thread.is_alive() for thread in threads):
            raise LaneError(
                "POWERBI_OUTPUT_BUDGET", "The Power BI reader exceeded its bounded response size."
            )
        if process.returncode:
            raise LaneError(
                "POWERBI_PARSE_FAILED",
                "The selected model did not satisfy the native format and resource limits.",
            )
        try:
            response = json.loads(output["stdout"])
        except (ValueError, RecursionError):
            raise LaneError(
                "POWERBI_RESPONSE_INVALID",
                "The native reader returned an invalid protocol response.",
            ) from None
    finally:
        group.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
    _, after = resolve_shared_asset(asset_id)
    if asset["files_sha256"] != after["files_sha256"]:
        raise LaneError("POWERBI_RUNTIME_CHANGED", "The reader runtime changed during extraction.")
    return response, {
        "asset_id": asset_id,
        "runtime_files_sha256": asset["files_sha256"],
        "installation_manifest_sha256": asset["installation_manifest_sha256"],
        "owner": "engine_owned_memory_limited_native_child",
        "process_memory_limit_bytes": 1_073_741_824,
        "host_identity": "not_attested",
        "source_bytes_mutated": False,
        "network_used": False,
        "embedded_content_executed": False,
    }
