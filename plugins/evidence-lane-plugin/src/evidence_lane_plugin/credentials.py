"""Noninteractive, current-Windows-user protection for local credentials."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

from .errors import LaneError


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(content: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise LaneError("PLATFORM_UNSUPPORTED", "Current-user DPAPI requires Windows.")
    if not content or len(content) > 65_536:
        raise LaneError("INVALID_CREDENTIAL", "The protected credential has an invalid size.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(content)).from_buffer_copy(content)
    source = _Blob(len(content), buffer)
    target = _Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    # A NULL description works for both signatures. No machine-wide flag is used.
    function.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise LaneError("CREDENTIAL_PROTECTION_FAILED", "Current-user credential protection failed.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def protect(value: str) -> str:
    return base64.b64encode(_dpapi(value.encode("utf-8"), decrypt=False)).decode("ascii")


def unprotect(value: str) -> str:
    try:
        if len(value) > 90_000:
            raise ValueError()
        content = base64.b64decode(value, validate=True)
        return _dpapi(content, decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError):
        raise LaneError("INVALID_CREDENTIAL", "The protected credential is invalid.") from None
