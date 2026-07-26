"""Immutable runtime identity for receipts and project-version manifests."""

from __future__ import annotations

import os
import shutil
import sqlite3

# Required for a fixed-argv Git identity probe; shell is never used.
import subprocess  # nosec B404
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .constants import ENGINE_VERSION, SCHEMA_VERSION
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .models import EngineIdentity


def source_tree_hash(package_root: str | Path) -> str:
    root = Path(package_root).resolve()
    members = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            members.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return sha256_bytes(canonical_json_bytes(members))


def toolchain_manifest() -> dict[str, Any]:
    packages = []
    for name in ("cryptography", "httpx", "mcp", "pytest"):
        try:
            package_version = version(name)
        except PackageNotFoundError:
            continue
        packages.append({"name": name, "version": package_version})
    return {
        "python": __import__("platform").python_version(),
        "implementation": __import__("platform").python_implementation(),
        "platform": __import__("platform").platform(),
        "sqlite": sqlite3.sqlite_version,
        "packages": packages,
    }


def git_source_commit(repository_root: str | Path) -> str:
    git_executable = shutil.which("git")
    if not git_executable:
        return "UNCOMMITTED"
    # The executable is resolved locally and every argument is fixed or a trusted root.
    completed = subprocess.run(  # nosec B603
        [
            git_executable,
            "-C",
            str(Path(repository_root).resolve()),
            "rev-parse",
            "HEAD",
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        close_fds=True,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    return (
        completed.stdout.strip().lower() if completed.returncode == 0 else "UNCOMMITTED"
    )


def build_engine_identity(
    *,
    package_root: str | Path,
    repository_root: str | Path,
) -> tuple[EngineIdentity, dict[str, Any]]:
    toolchain = toolchain_manifest()
    package_sha = source_tree_hash(package_root)
    identity = EngineIdentity(
        repository="https://github.com/rathee000001/evidence_lane_plugin",
        commit=git_source_commit(repository_root),
        release=ENGINE_VERSION,
        package_sha256=package_sha,
        schema_version=SCHEMA_VERSION,
        toolchain_manifest_sha256=sha256_bytes(canonical_json_bytes(toolchain)),
        signature_key_id=None,
        signature_verified=False,
    )
    return identity, toolchain
