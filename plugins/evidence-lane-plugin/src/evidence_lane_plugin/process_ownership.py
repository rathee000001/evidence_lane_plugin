"""Windows kernel-owned child lifetime; process IDs are never takeover authority."""

from __future__ import annotations

import ctypes
import os
from uuid import uuid4

from .errors import LaneError


def kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    api.CreateJobObjectW.restype = ctypes.c_void_p
    api.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    api.OpenJobObjectW.restype = ctypes.c_void_p
    api.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    api.SetInformationJobObject.restype = ctypes.c_int
    api.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    api.AssignProcessToJobObject.restype = ctypes.c_int
    api.GetCurrentProcess.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    return api


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", ctypes.c_uint32), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", ctypes.c_uint32),
                ("affinity", ctypes.c_size_t), ("priority", ctypes.c_uint32), ("scheduling", ctypes.c_uint32)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class ChildProcessGroup:
    def __init__(self):
        self.handle = None
        self.name = None

    def start(self) -> None:
        if os.name != "nt":
            return  # Other operating systems are not qualified by the Windows runtime tests.
        if self.handle:
            raise LaneError("PROCESS_GROUP_ALREADY_STARTED", "The child lifetime group is already active.")
        api = kernel()
        name = "Local\\EvidenceLaneWorkers-" + str(uuid4())
        handle = api.CreateJobObjectW(None, name)
        if not handle:
            raise LaneError("PROCESS_GROUP_UNAVAILABLE", "Windows could not create the worker lifetime group.")
        if ctypes.get_last_error() == 183:
            api.CloseHandle(handle)
            raise LaneError("PROCESS_GROUP_COLLISION", "The child lifetime group name is already in use.")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            api.CloseHandle(handle)
            raise LaneError("PROCESS_GROUP_UNAVAILABLE", "Windows could not bind child lifetime to this engine.")
        self.handle, self.name = handle, name

    def close(self) -> None:
        if self.handle:
            kernel().CloseHandle(self.handle)
            self.handle = None
            self.name = None


def join_child_group(name: str | None) -> None:
    """A new worker joins before acknowledging initialization or accepting work."""
    if name is None:
        return
    api = kernel()
    handle = api.OpenJobObjectW(0x0001, False, name)  # JOB_OBJECT_ASSIGN_PROCESS only.
    if not handle:
        raise LaneError("WORKER_OWNER_GONE", "The engine lifetime handle is unavailable.")
    try:
        if not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
            raise LaneError("WORKER_LIFETIME_BINDING_FAILED", "The new worker could not join its engine lifetime group.")
    finally:
        api.CloseHandle(handle)  # Only the engine retains a handle; its death closes the last one.
def codec_python_executable():
    """Use the engine's interpreter, bypassing Windows venv redirector processes.

    Codec children receive the engine's explicit import roots with -I -S. A
    venv launcher would consume another Job Object process slot before the
    actual codec interpreter starts, leaving no slot for its native tool.
    """
    import sys
    from pathlib import Path

    return Path(getattr(sys, '_base_executable', None) or sys.executable).resolve(strict=True)

