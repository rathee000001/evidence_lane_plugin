"""Gate PDF codecs behind owned processes with memory, time and output limits."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from .hashing import canonical_json_bytes
from .pdf_parsers import digest, fail
from .process_ownership import ChildProcessGroup, ExtendedLimits, codec_python_executable, kernel


def invoke_pdf(operation, arguments, *, timeout_seconds=90):
    if operation not in {"parse", "generate", "edit", "render", "ocr", "enrich"}:
        fail("OPERATION_INVALID")
    # These are the engine's configured, already trusted import roots. The
    # client can supply neither import roots, an executable nor a child script.
    paths = [str(Path(path).resolve()) for path in sys.path if path and Path(path).is_dir()]
    request = {"operation": operation, "arguments": arguments, "engine_import_roots": paths}
    body = canonical_json_bytes(request)
    if len(body) > 33_554_432:
        fail("INPUT_BUDGET")
    script = Path(__file__).with_name("pdf_child.py")
    script_sha = digest(script.read_bytes())
    executable = codec_python_executable()
    executable_sha = digest(executable.read_bytes())
    group, process = ChildProcessGroup(), None
    group.start()
    memory = 6_442_450_944 if operation == "enrich" else 2_147_483_648
    try:
        if os.name == "nt":
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000 | 0x100 | 0x8
            limits.basic.active_process_limit = (
                2  # One PDF child plus its selected native OCR tool.
            )
            limits.process_memory = memory
            if not kernel().SetInformationJobObject(
                group.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                fail("OWNER_UNAVAILABLE")
        environment = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")
        }
        environment.update(
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONNOUSERSITE="1",
            HF_HUB_OFFLINE="1",
            HF_DATASETS_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        process = subprocess.Popen(
            [str(executable), "-I", "-S", "-B", str(script)],
            cwd=script.parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
        if os.name == "nt" and not kernel().AssignProcessToJobObject(
            group.handle, int(process._handle)
        ):
            fail("OWNER_UNAVAILABLE")
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
            threading.Thread(target=drain, args=("stdout", 67_108_864), daemon=True),
            threading.Thread(target=drain, args=("stderr", 65536), daemon=True),
            threading.Thread(target=send, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            fail("OPERATION_TIMEOUT")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
        if overflow or any(thread.is_alive() for thread in threads):
            fail("OUTPUT_BUDGET")
        if process.returncode:
            fail("NATIVE_OPERATION_FAILED")
        try:
            response = json.loads(output["stdout"])
        except (ValueError, RecursionError):
            fail("RESPONSE_INVALID")
        if response.get("status") != "ok":
            code = response.get("code", "")
            from .errors import LaneError

            if (
                isinstance(code, str)
                and code.startswith("PDF_")
                and len(code) < 80
                and code.replace("_", "").isalnum()
            ):
                raise LaneError(code, "The owned PDF operation rejected the requested input.")
            fail("NATIVE_OPERATION_FAILED")
    finally:
        group.close()
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
    if digest(script.read_bytes()) != script_sha or digest(executable.read_bytes()) != executable_sha:
        fail("RUNTIME_CHANGED")
    result = response["result"]
    result["process_evidence"] = {
        "owner": "engine_owned_pdf_child",
        "script_sha256": script_sha,
        "interpreter_sha256": executable_sha,
        "interpreter_basis": "engine_base_interpreter_with_explicit_import_roots",
        "process_memory_limit_bytes": memory,
        "time_limit_seconds": timeout_seconds,
        "memory_limit_mechanism": "Windows_Job_Object" if os.name == "nt" else "POSIX_RLIMIT_AS",
        "network_policy": "python_socket_audit_denied",
        "embedded_actions_executed": False,
        "host_identity": "not_attested",
    }
    return result
