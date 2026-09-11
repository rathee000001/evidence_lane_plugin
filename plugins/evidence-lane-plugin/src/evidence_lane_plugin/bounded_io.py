"""Shared pre-allocation file and subprocess I/O budgets."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess  # nosec B404 - argv-only bounded launcher below
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require

DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_FILE_COUNT = 25_000
# The governed Task11 worktree has a bounded 1.886 GB dirty-content set. Four
# GiB leaves finite headroom for one Delta while retaining a hard request cap.
DEFAULT_MAX_AGGREGATE_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_STDOUT_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_STDERR_BYTES = 4 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024


@dataclass(slots=True)
class IOBudget:
    """Mutable request-local budget checked before content allocation."""

    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_file_count: int = DEFAULT_MAX_FILE_COUNT
    max_aggregate_bytes: int = DEFAULT_MAX_AGGREGATE_BYTES
    consumed_file_count: int = 0
    consumed_bytes: int = 0

    def reserve(self, *, size_bytes: int) -> None:
        require(
            0 <= size_bytes <= self.max_file_bytes,
            "BOUNDED_IO_FILE_BYTES_EXCEEDED",
            "A file exceeded the governed per-file byte budget.",
            status="BLOCKED",
            observed_bytes=size_bytes,
            max_file_bytes=self.max_file_bytes,
        )
        require(
            self.consumed_file_count + 1 <= self.max_file_count
            and self.consumed_bytes + size_bytes <= self.max_aggregate_bytes,
            "BOUNDED_IO_AGGREGATE_BUDGET_EXCEEDED",
            "The request exceeded its governed file-count or aggregate-byte budget.",
            status="BLOCKED",
            max_file_count=self.max_file_count,
            max_aggregate_bytes=self.max_aggregate_bytes,
        )
        self.consumed_file_count += 1
        self.consumed_bytes += size_bytes

    def receipt(self) -> dict[str, int]:
        return {
            "max_file_bytes": self.max_file_bytes,
            "max_file_count": self.max_file_count,
            "max_aggregate_bytes": self.max_aggregate_bytes,
            "consumed_file_count": self.consumed_file_count,
            "consumed_bytes": self.consumed_bytes,
        }


def _bounded_target(path: str | Path, *, root: str | Path | None) -> Path:
    target = Path(path).expanduser()
    lexical = Path(os.path.abspath(target))
    if root is not None:
        lexical_root = Path(os.path.abspath(Path(root).expanduser()))
        authority_root = lexical_root.resolve(strict=True)
        try:
            relative = lexical.relative_to(lexical_root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "BOUNDED_IO_PATH_ESCAPE",
                "A bounded file path escaped its authority root.",
                status="BLOCKED",
            ) from exc
        cursor = lexical_root
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        for component in relative.parts:
            cursor /= component
            metadata = cursor.lstat()
            if cursor.is_symlink() or (
                reparse_flag
                and int(getattr(metadata, "st_file_attributes", 0)) & reparse_flag
            ):
                raise EvidenceLaneError(
                    "BOUNDED_IO_REPARSE_PATH_BLOCKED",
                    "A bounded file path crossed a symlink or reparse point.",
                    status="BLOCKED",
                )
        resolved = lexical.resolve(strict=True)
        try:
            resolved.relative_to(authority_root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "BOUNDED_IO_RESOLVED_PATH_ESCAPE",
                "A bounded file path resolved outside its authority root.",
                status="BLOCKED",
            ) from exc
    return lexical


def bounded_existing_path(
    path: str | Path,
    *,
    root: str | Path,
) -> Path:
    """Return an existing path only when every descendant component is in-root."""

    return _bounded_target(path, root=root)


def bounded_file_identity(
    path: str | Path,
    *,
    budget: IOBudget,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Stream one stable regular file through a no-follow descriptor when supported."""

    target = _bounded_target(path, root=root)
    before_lstat = target.lstat()
    require(
        stat.S_ISREG(before_lstat.st_mode) and not target.is_symlink(),
        "BOUNDED_IO_REGULAR_FILE_REQUIRED",
        "Bounded file input must be a non-symlink regular file.",
        status="BLOCKED",
    )
    budget.reserve(size_bytes=before_lstat.st_size)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    no_follow_flag = int(getattr(os, "O_NOFOLLOW", 0))
    no_follow_supported = bool(no_follow_flag)
    if no_follow_supported:
        flags |= no_follow_flag
    descriptor = os.open(target, flags)
    try:
        before = os.fstat(descriptor)
        require(
            stat.S_ISREG(before.st_mode) and before.st_size == before_lstat.st_size,
            "BOUNDED_IO_DESCRIPTOR_IDENTITY_MISMATCH",
            "The opened file descriptor did not match the preflight identity.",
            status="MISMATCH",
        )
        digest = hashlib.sha256()
        observed = 0
        while True:
            block = os.read(descriptor, _STREAM_CHUNK_BYTES)
            if not block:
                break
            observed += len(block)
            require(
                observed <= before.st_size and observed <= budget.max_file_bytes,
                "BOUNDED_IO_STREAM_OVERFLOW",
                "A file grew beyond its preflight byte budget while streaming.",
                status="MISMATCH",
            )
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    require(
        observed == before.st_size
        and all(getattr(before, field) == getattr(after, field) for field in stable_fields),
        "BOUNDED_IO_FILE_CHANGED_DURING_READ",
        "A bounded file changed while its identity was being streamed.",
        status="MISMATCH",
    )
    return {
        "sha256": digest.hexdigest().upper(),
        "size_bytes": observed,
        "streamed": True,
        "descriptor_identity_stable": True,
        "symlink_followed": False,
        "os_no_follow_flag_used": no_follow_supported,
    }


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class BoundedProcessDigestResult:
    returncode: int
    stdout_sha256: str
    stdout_bytes: int
    stderr: bytes


def run_bounded_process(
    command: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 120,
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES,
) -> BoundedProcessResult:
    """Run one argv-only process with independently capped output streams."""

    require(
        bool(command)
        and timeout_seconds > 0
        and max_stdout_bytes > 0
        and max_stderr_bytes > 0,
        "BOUNDED_PROCESS_CONTRACT_INVALID",
        "A bounded process requires argv, timeout, and positive stream limits.",
        status="BLOCKED",
    )
    process = subprocess.Popen(  # nosec B603 - exact argv, shell disabled
        [str(value) for value in command],
        cwd=str(Path(cwd).resolve()),
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    outputs: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    overflow: list[str] = []

    def drain(name: str, limit: int) -> None:
        stream = process.stdout if name == "stdout" else process.stderr
        assert stream is not None
        while block := stream.read(64 * 1024):
            if len(outputs[name]) + len(block) > limit:
                overflow.append(name)
                process.kill()
                return
            outputs[name].extend(block)

    threads = [
        threading.Thread(target=drain, args=("stdout", max_stdout_bytes), daemon=True),
        threading.Thread(target=drain, args=("stderr", max_stderr_bytes), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=5)
        raise EvidenceLaneError(
            "BOUNDED_PROCESS_TIMEOUT",
            "A bounded subprocess exceeded its duration budget and was terminated.",
            status="BLOCKED",
            details={"timeout_seconds": timeout_seconds},
        ) from exc
    for thread in threads:
        thread.join(timeout=5)
    if overflow:
        raise EvidenceLaneError(
            "BOUNDED_PROCESS_OUTPUT_EXCEEDED",
            "A subprocess exceeded its stdout or stderr byte budget and was terminated.",
            status="BLOCKED",
            details={"streams": sorted(set(overflow))},
        )
    return BoundedProcessResult(
        returncode=returncode,
        stdout=bytes(outputs["stdout"]),
        stderr=bytes(outputs["stderr"]),
    )


def run_owned_bounded_process(command, *, cwd, env=None, timeout_seconds=120,
                              max_stdout_bytes=DEFAULT_MAX_STDOUT_BYTES,
                              max_stderr_bytes=DEFAULT_MAX_STDERR_BYTES, check=None):
    """Bound one tool and descendants; release argv only after Windows ownership.

    Trusted adapters supply argv after their own authority checks. Git uses
    fixed transport/provider contracts; CI uses an exact task-bound policy.
    """
    import ctypes
    import json
    import signal

    from .errors import LaneError
    from .process_ownership import (
        ChildProcessGroup,
        ExtendedLimits,
        codec_python_executable,
        kernel,
    )
    require(bool(command) and Path(command[0]).is_absolute() and timeout_seconds > 0
        and max_stdout_bytes > 0 and max_stderr_bytes > 0 and (check is None or callable(check)),
        'BOUNDED_PROCESS_CONTRACT_INVALID', 'Use an absolute tool and positive process bounds.')
    if check is not None:
        check()
    body = json.dumps(list(command), ensure_ascii=True).encode()
    require(len(body) <= 65536, 'BOUNDED_PROCESS_CONTRACT_INVALID', 'The bounded argv exceeds 64 KiB.')
    group, process = ChildProcessGroup(), None
    output, overflow, threads = {'stdout': bytearray(), 'stderr': bytearray()}, [], []
    environment = {key: value for key, value in (env if env is not None else os.environ).items()
                   if not key.upper().startswith('PYTHON')}
    group.start()
    try:
        if os.name == 'nt':
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000 | 0x8 | 0x200
            limits.basic.active_process_limit = 32
            limits.job_memory = 4 * 1024 * 1024 * 1024
            if not kernel().SetInformationJobObject(group.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise LaneError('PROCESS_GROUP_UNAVAILABLE', 'The tool process limits could not be applied.')
        # A venv redirector can spawn the real interpreter before assignment.
        # Launch the base interpreter directly so the pipe-waiting bootstrap
        # itself owns the Job membership before any command can be released.
        process = subprocess.Popen([str(codec_python_executable()), '-I', '-S', '-B',
            str(Path(__file__).with_name('_bounded_process_child.py'))],
            cwd=str(Path(cwd).resolve()), env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, close_fds=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), start_new_session=os.name != 'nt')
        if os.name == 'nt' and not kernel().AssignProcessToJobObject(group.handle, int(process._handle)):
            raise LaneError('PROCESS_GROUP_UNAVAILABLE', 'The tool bootstrap could not join its lifetime group.')
        def drain(name, limit):
            stream = getattr(process, name)
            try:
                while block := stream.read(65536):
                    if len(output[name]) + len(block) > limit:
                        overflow.append(name)
                        process.kill()
                        return
                    output[name].extend(block)
            finally:
                stream.close()
        threads = [threading.Thread(target=drain, args=(name, limit), daemon=True)
            for name, limit in [('stdout', max_stdout_bytes), ('stderr', max_stderr_bytes)]]
        for thread in threads:
            thread.start()
        process.stdin.write(body)
        process.stdin.close()
        deadline = time.monotonic() + timeout_seconds
        while True:
            if check is not None:
                check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LaneError('BOUNDED_PROCESS_TIMEOUT', 'The tool and its descendants exceeded the duration budget.')
            try:
                code = process.wait(timeout=remaining if check is None else min(remaining, 0.25))
                break
            except subprocess.TimeoutExpired:
                if check is None:
                    raise LaneError('BOUNDED_PROCESS_TIMEOUT', 'The tool and its descendants exceeded the duration budget.') from None
        if check is not None:
            check()
    finally:
        # Kill descendants before joining readers; a grandchild may hold a pipe.
        group.close()
        if process is not None:
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
    if overflow or any(thread.is_alive() for thread in threads):
        raise LaneError('BOUNDED_PROCESS_OUTPUT_EXCEEDED', 'The tool exceeded its output bounds.')
    return BoundedProcessResult(code, bytes(output['stdout']), bytes(output['stderr']))


def run_bounded_process_digest(
    command: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 120,
    max_stdout_bytes: int = DEFAULT_MAX_AGGREGATE_BYTES,
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES,
) -> BoundedProcessDigestResult:
    """Hash bounded stdout as it streams, retaining only bounded stderr."""

    require(
        bool(command)
        and timeout_seconds > 0
        and max_stdout_bytes > 0
        and max_stderr_bytes > 0,
        "BOUNDED_PROCESS_CONTRACT_INVALID",
        "A bounded process digest requires argv, timeout, and positive limits.",
        status="BLOCKED",
    )
    process = subprocess.Popen(  # nosec B603 - exact argv, shell disabled
        [str(value) for value in command],
        cwd=str(Path(cwd).resolve()),
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    stdout_digest = hashlib.sha256()
    stdout_count = [0]
    stderr = bytearray()
    overflow: list[str] = []

    def drain_stdout() -> None:
        assert process.stdout is not None
        while block := process.stdout.read(64 * 1024):
            stdout_count[0] += len(block)
            if stdout_count[0] > max_stdout_bytes:
                overflow.append("stdout")
                process.kill()
                return
            stdout_digest.update(block)

    def drain_stderr() -> None:
        assert process.stderr is not None
        while block := process.stderr.read(64 * 1024):
            if len(stderr) + len(block) > max_stderr_bytes:
                overflow.append("stderr")
                process.kill()
                return
            stderr.extend(block)

    threads = [
        threading.Thread(target=drain_stdout, daemon=True),
        threading.Thread(target=drain_stderr, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=5)
        raise EvidenceLaneError(
            "BOUNDED_PROCESS_TIMEOUT",
            "A bounded subprocess digest exceeded its duration budget.",
            status="BLOCKED",
            details={"timeout_seconds": timeout_seconds},
        ) from exc
    for thread in threads:
        thread.join(timeout=5)
    if overflow:
        raise EvidenceLaneError(
            "BOUNDED_PROCESS_OUTPUT_EXCEEDED",
            "A subprocess digest exceeded its stdout or stderr byte budget.",
            status="BLOCKED",
            details={"streams": sorted(set(overflow))},
        )
    return BoundedProcessDigestResult(
        returncode=returncode,
        stdout_sha256=stdout_digest.hexdigest().upper(),
        stdout_bytes=stdout_count[0],
        stderr=bytes(stderr),
    )
