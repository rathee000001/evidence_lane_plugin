"""Immutable runtime identity for receipts and project-version manifests."""

from __future__ import annotations

import os
import re
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

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def identity_repository_root(package_file: str | Path) -> Path:
    """Locate the identity root for a source checkout or copied Codex cache.

    A development checkout must bind to the enclosing Git repository.  A Codex
    marketplace installation has no ``.git`` directory inside its versioned
    cache, so the version root itself must reach the marketplace verifier.
    """

    source = Path(package_file).resolve()
    for ancestor in source.parents:
        if (ancestor / ".git").exists():
            return ancestor
    parents = source.parents
    if len(parents) >= 3 and parents[1].name == "src":
        return parents[2]
    return source.parent


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


def _run_git(
    git_executable: str,
    repository_root: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [git_executable, "-C", str(repository_root), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        close_fds=True,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )


def _direct_git_commit(git_executable: str, repository_root: Path) -> str | None:
    completed = _run_git(git_executable, repository_root, ["rev-parse", "HEAD"])
    candidate = completed.stdout.strip().lower()
    if completed.returncode == 0 and _COMMIT_RE.fullmatch(candidate):
        return candidate
    return None


def _verified_codex_marketplace_commit(
    git_executable: str,
    installed_plugin_root: Path,
) -> str | None:
    """Bind one copied Codex cache to the exact Git marketplace snapshot."""

    parents = installed_plugin_root.parents
    if (
        len(parents) < 5
        or parents[2].name.casefold() != "cache"
        or parents[3].name.casefold() != "plugins"
    ):
        return None
    marketplace_name = parents[1].name
    plugin_name = parents[0].name
    codex_home = parents[4]
    marketplace_root = (
        codex_home / ".tmp" / "marketplaces" / marketplace_name
    ).resolve()
    source_plugin_root = (marketplace_root / "plugins" / plugin_name).resolve()
    if not source_plugin_root.is_dir():
        return None
    commit = _direct_git_commit(git_executable, marketplace_root)
    if commit is None:
        return None
    plugin_selector = f"plugins/{plugin_name}"
    clean = _run_git(
        git_executable,
        marketplace_root,
        ["diff", "--quiet", "HEAD", "--", plugin_selector],
    )
    if clean.returncode != 0:
        return None
    tracked = _run_git(
        git_executable,
        marketplace_root,
        ["ls-files", "-z", "--", plugin_selector],
    )
    if tracked.returncode != 0:
        return None
    tracked_paths = [item for item in tracked.stdout.split("\0") if item]
    if not tracked_paths:
        return None
    tracked_prefix = Path("plugins") / plugin_name
    installed_root = installed_plugin_root.resolve()
    for tracked_path in tracked_paths:
        try:
            relative = Path(tracked_path).relative_to(tracked_prefix)
            source = (marketplace_root / tracked_path).resolve()
            installed = (installed_root / relative).resolve()
            source.relative_to(source_plugin_root)
            installed.relative_to(installed_root)
        except ValueError:
            return None
        if (
            not source.is_file()
            or not installed.is_file()
            or sha256_file(source) != sha256_file(installed)
        ):
            return None
    return commit


def git_source_commit(repository_root: str | Path) -> str:
    git_executable = shutil.which("git")
    if not git_executable:
        return "UNCOMMITTED"
    root = Path(repository_root).resolve()
    direct = _direct_git_commit(git_executable, root)
    if direct is not None:
        return direct
    marketplace = _verified_codex_marketplace_commit(
        git_executable,
        root,
    )
    return marketplace or "UNCOMMITTED"


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
