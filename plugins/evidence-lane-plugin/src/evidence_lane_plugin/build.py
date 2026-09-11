"""Deterministic package content identity, distinct from installation evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .errors import LaneError
from .storage import json_text, reject_links


def runtime_source_identity() -> dict:
    """Fingerprint the selected executable package bytes, without claiming installation.

    Captured at engine construction and independently by its local adapter.
    This is package-file evidence, not native host or process attestation.
    """
    module_root = Path(__file__).resolve().parent
    root = module_root.parents[1]
    content = package_contents(root)
    return {'source_digest': content['content_digest'],
        'module_root': str(module_root), 'package_root': str(root), 'member_count': len(content['files']),
        'basis': 'complete_package_files_at_engine_construction', 'native_installation_verified': False}

PACKAGE_DIRECTORIES = frozenset(
    {".codex-plugin", "src", "scripts", "skills", "hooks", "contracts", "assets",
     "authorities", "sectors", "env", "uop", "schemas", "sdk", "mcp",
     "manifests", "toolchains", "tests", "provisioning", "studio"}
)
PACKAGE_FILES = frozenset(
    {".mcp.json", "README.md", "LICENSE.md", "COPYRIGHT.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml",
     "requirements.lock.txt", "requirements.toolchain.lock.txt", "requirements.onnx-directml.lock.txt",
     "requirements.torch-cpu.lock.txt", "requirements.torch-nvidia.lock.txt",
     "requirements.runtime.lock.txt", "requirements.runtime-lock.v4.json"}
)


def package_contents(root: Path) -> dict:
    root = root.resolve(strict=True)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    reject_links(manifest_path, root)
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        metadata.get("name") != "evidence-lane-plugin"
        or metadata.get("version", "").split("+")[0] != "4.0.3"
    ):
        raise LaneError("PACKAGE_IDENTITY_INVALID", "A canonical v4 plugin manifest is required.")
    files: list[dict] = []
    total = 0

    def visit(directory: Path) -> None:
        nonlocal total
        for member in sorted(directory.iterdir(), key=lambda p: p.name):
            reject_links(member, root)
            if member.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache"} or member.suffix in {".pyc", ".pyo"}:
                continue
            relative = member.relative_to(root)
            if (
                relative.parts[0] not in PACKAGE_DIRECTORIES
                and relative.as_posix() not in PACKAGE_FILES
            ):
                # The content identity enumerates files, as Git and the archive
                # do. An empty source directory has no packaged member; any
                # unapproved file or nonempty directory remains an error.
                if member.is_dir() and not any(member.iterdir()):
                    continue
                raise LaneError(
                    "UNEXPECTED_PACKAGE_MEMBER",
                    "The package contains an unapproved top-level member.",
                    details={"member": relative.as_posix()},
                )
            if member.is_dir():
                visit(member)
                continue
            if not member.is_file() or member.stat().st_size > 64 * 1024 * 1024:
                raise LaneError(
                    "PACKAGE_MEMBER_INVALID", "Package members must be bounded regular files."
                )
            content = member.read_bytes()
            total += len(content)
            if len(files) >= 16_384 or total > 256 * 1024 * 1024:
                raise LaneError(
                    "PACKAGE_TOO_LARGE",
                    "Shared runtime dependencies must remain outside the plugin.",
                )
            files.append(
                {
                    "path": relative.as_posix(),
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )

    visit(root)
    content = {
        "schema_version": 1,
        "plugin_id": metadata["name"],
        "version": metadata["version"],
        "files": files,
        "total_bytes": total,
    }
    content["content_digest"] = hashlib.sha256(json_text(content).encode("utf-8")).hexdigest()
    return content


def build_identity(root: Path, *, allow_dirty: bool = False) -> dict:
    from .git_adapter import resolve_git_executable

    git_executable = resolve_git_executable(root)

    def git(*args: str) -> str:
        result = subprocess.run(
            [git_executable, *args],
            cwd=root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise LaneError(
                "BUILD_SOURCE_UNAVAILABLE",
                "Build identity requires a readable Git source checkout.",
            )
        return result.stdout.strip()

    head = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=all", "--", "."))
    if dirty and not allow_dirty:
        raise LaneError(
            "UNCOMMITTED_PACKAGE", "Commit the complete package before creating a release identity."
        )
    content = package_contents(root)
    core = {
        "git_head": head,
        "source_state": "working_tree" if dirty else "commit",
        "package": content,
    }
    core["build_id"] = hashlib.sha256(json_text(core).encode("utf-8")).hexdigest()
    return core
