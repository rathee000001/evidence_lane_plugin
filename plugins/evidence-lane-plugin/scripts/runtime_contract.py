"""Content-addressed durable runtime layout for the Codex plugin launcher.

Codex is allowed to reconstruct its marketplace cache during application
startup.  Derived dependencies therefore cannot live only below that cache.
This module keeps the Python environment in the user's durable Evidence Lane
installation root while the active plugin source remains authoritative.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

RUNTIME_SCHEMA = "evidence-lane.codex-native-runtime.v1"
MARKER_SCHEMA = "evidence-lane.codex-native-runtime-ready.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return normalized or "unknown"


def data_root() -> Path:
    configured = os.environ.get("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT")
    if configured is not None:
        if not configured.strip():
            raise RuntimeError(
                "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT cannot be empty when configured."
            )
        resolved = Path(configured).expanduser().resolve()
    else:
        resolved = (
            Path.home()
            / ".codex"
            / "plugins"
            / "runtime"
            / "evidence-lane-plugin"
        ).resolve()
    return resolved


def runtime_identity(plugin_root: Path) -> dict[str, Any]:
    lock = plugin_root / "requirements.lock.txt"
    torch_lock = plugin_root / "requirements.torch-cpu.lock.txt"
    toolchain_lock = plugin_root / "requirements.toolchain.lock.txt"
    if not lock.is_file() or not torch_lock.is_file() or not toolchain_lock.is_file():
        raise RuntimeError(
            "Missing pinned CPU Torch, base, or full-toolchain dependency lock."
        )
    core = {
        "schema": RUNTIME_SCHEMA,
        "requirements_lock_sha256": _sha256(lock),
        "requirements_torch_cpu_lock_sha256": _sha256(torch_lock),
        "requirements_toolchain_lock_sha256": _sha256(toolchain_lock),
        "python_implementation": platform.python_implementation(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_cache_tag": str(sys.implementation.cache_tag or "unknown"),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
    }
    core["runtime_key"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    return core


def runtime_projection_root(plugin_root: Path) -> Path:
    identity = runtime_identity(plugin_root)
    # Windows extension loaders still encounter legacy path-length limits.
    # The directory uses a compact 128-bit prefix while the marker below seals
    # and validates the complete 256-bit runtime identity.
    key = _safe_component(str(identity["runtime_key"])[:32])
    return (data_root() / "runtime" / "codex" / key).resolve()


def runtime_environment(plugin_root: Path) -> Path:
    return runtime_projection_root(plugin_root) / "venv"


def runtime_marker(plugin_root: Path) -> Path:
    return runtime_projection_root(plugin_root) / "RUNTIME_READY.json"


def marker_payload(plugin_root: Path) -> dict[str, Any]:
    return {
        "schema": MARKER_SCHEMA,
        "status": "PASS",
        "runtime_identity": runtime_identity(plugin_root),
    }


def write_marker(plugin_root: Path, marker: Path) -> dict[str, Any]:
    payload = marker_payload(plugin_root)
    payload["receipt_sha256"] = hashlib.sha256(_json_bytes(payload)).hexdigest().upper()
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(marker.suffix + ".tmp")
    temporary.write_bytes(_json_bytes(payload) + b"\n")
    temporary.replace(marker)
    return payload


def marker_is_valid(plugin_root: Path, marker: Path) -> bool:
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        receipt = str(payload.pop("receipt_sha256"))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if receipt != hashlib.sha256(_json_bytes(payload)).hexdigest().upper():
        return False
    return payload == marker_payload(plugin_root)
