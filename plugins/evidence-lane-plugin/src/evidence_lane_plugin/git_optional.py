"""Truthful optional Git arm for source intake and history enrichment."""

from __future__ import annotations

import importlib.util
import os
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .git_adapter import try_resolve_git_executable

GIT_ARM_MODES = ("AUTO", "REQUIRED", "DISABLED")


def _run_git(
    executable: str, root: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [executable, "-C", str(root), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )


def normalize_git_arm_mode(value: str | None) -> str:
    mode = str(value or "AUTO").strip().replace("-", "_").upper()
    aliases = {
        "OPTIONAL": "AUTO",
        "ENABLED": "REQUIRED",
        "ON": "REQUIRED",
        "OFF": "DISABLED",
        "NONE": "DISABLED",
    }
    mode = aliases.get(mode, mode)
    require(
        mode in GIT_ARM_MODES,
        "GIT_ARM_MODE_INVALID",
        "Git arm mode must be AUTO, REQUIRED, or DISABLED.",
        status="BLOCKED",
        provided=value,
        supported=list(GIT_ARM_MODES),
    )
    return mode


def _exact_worktree_root(executable: str, root: Path) -> bool:
    top = _run_git(executable, root, 'rev-parse', '--show-toplevel')
    return top.returncode == 0 and bool(top.stdout.strip()) and Path(top.stdout.strip()).resolve() == root.resolve()


def probe_git_arm(
    repository_root: str | Path,
    *,
    requested_mode: str | None = "AUTO",
) -> dict[str, Any]:
    """Select Git history enrichment without making Git a source-intake prerequisite."""

    root = Path(repository_root).expanduser().resolve()
    require(
        root.is_dir(),
        "GIT_ARM_SOURCE_DIRECTORY_MISSING",
        "The optional Git arm requires an existing source directory.",
        status="MISMATCH",
        repository_root=str(root),
    )
    mode = normalize_git_arm_mode(requested_mode)
    base = {
        "schema": "evidence-lane.git-optional-arm.v1",
        "requested_mode": mode,
        "repository_root": str(root),
        "fallback_content_index_enabled": True,
        "remote_write_authorized": False,
    }
    if mode == "DISABLED":
        return {
            **base,
            "status": "PASS",
            "state": "DISABLED_BY_USER",
            "history_index_enabled": False,
            "git_executable_available": bool(try_resolve_git_executable(root)),
            "repository_is_git": None,
            "reason": "The user disabled Git enrichment for this intake.",
        }

    executable = try_resolve_git_executable(root)
    if not executable:
        if mode == "REQUIRED":
            raise EvidenceLaneError(
                "GIT_ARM_REQUIRED_EXECUTABLE_MISSING",
                "Git enrichment was required, but no Git executable is available.",
                status="BLOCKED",
            )
        return {
            **base,
            "status": "PASS",
            "state": "UNAVAILABLE_FALLBACK",
            "history_index_enabled": False,
            "git_executable_available": False,
            "repository_is_git": False,
            "reason": "Git is unavailable; deterministic content indexing remains active.",
        }

    completed = _run_git(executable, root, "rev-parse", "--is-inside-work-tree")
    inside = completed.returncode == 0 and completed.stdout.strip() == "true"
    # Git searches parent directories automatically. A selected content folder
    # must not inherit its parent's repository identity or worktree status.
    exact_root = _exact_worktree_root(executable, root) if inside else False
    if inside and not exact_root:
        if mode == 'REQUIRED':
            raise EvidenceLaneError('GIT_ARM_EXACT_ROOT_REQUIRED',
                'Git history requires the exact selected worktree root, not a content folder inside another repository.',
                status='BLOCKED')
        return {**base, 'status': 'PASS', 'state': 'PARENT_GIT_WORKTREE_NOT_ADMITTED',
            'history_index_enabled': False, 'git_executable_available': True, 'repository_is_git': False,
            'reason': 'The selected folder uses content indexing; its parent Git worktree was not selected.'}
    if not inside:
        if mode == "REQUIRED":
            raise EvidenceLaneError(
                "GIT_ARM_REQUIRED_WORKTREE_MISSING",
                "Git enrichment was required, but the source is not a Git worktree.",
                status="BLOCKED",
                details={"repository_root": str(root)},
            )
        return {
            **base,
            "status": "PASS",
            "state": "NOT_A_GIT_WORKTREE_FALLBACK",
            "history_index_enabled": False,
            "git_executable_available": True,
            "repository_is_git": False,
            "reason": "The source has no Git worktree; content indexing remains active.",
        }

    head = _run_git(executable, root, "rev-parse", "HEAD")
    if head.returncode != 0:
        if mode == "REQUIRED":
            raise EvidenceLaneError(
                "GIT_ARM_REQUIRED_HEAD_MISSING",
                "Git enrichment was required, but the worktree has no readable HEAD.",
                status="BLOCKED",
            )
        return {
            **base,
            "status": "PASS",
            "state": "GIT_HEAD_UNAVAILABLE_FALLBACK",
            "history_index_enabled": False,
            "git_executable_available": True,
            "repository_is_git": True,
            "reason": "Git metadata exists without a readable HEAD; content indexing remains active.",
        }
    tree = _run_git(executable, root, "rev-parse", "HEAD^{tree}")
    branch = _run_git(
        executable, root, "symbolic-ref", "--quiet", "--short", "HEAD"
    )
    status = _run_git(
        executable, root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if tree.returncode != 0 or status.returncode != 0:
        if mode == "REQUIRED":
            raise EvidenceLaneError(
                "GIT_ARM_REQUIRED_IDENTITY_INCOMPLETE",
                "Git enrichment was required, but tree or worktree state is unreadable.",
                status="BLOCKED",
            )
        return {
            **base,
            "status": "PASS",
            "state": "GIT_IDENTITY_INCOMPLETE_FALLBACK",
            "history_index_enabled": False,
            "git_executable_available": True,
            "repository_is_git": True,
            "reason": (
                "Git HEAD exists, but tree or worktree state is unreadable; "
                "content indexing remains active."
            ),
        }
    status_entries = [line for line in status.stdout.splitlines() if line.strip()]
    gitpython: dict[str, Any]
    if importlib.util.find_spec("git") is None:
        gitpython = {
            "status": "UNAVAILABLE",
            "parity": None,
        }
    else:
        from git import Repo  # type: ignore[import-not-found]

        repository = Repo(root)
        gitpython_head = str(repository.head.commit.hexsha).lower()
        gitpython_tree = str(repository.head.commit.tree.hexsha).lower()
        gitpython_dirty = bool(repository.is_dirty(untracked_files=True))
        parity = (
            gitpython_head == head.stdout.strip().lower()
            and gitpython_tree == tree.stdout.strip().lower()
            and gitpython_dirty == bool(status_entries)
        )
        require(
            parity,
            "GIT_CLI_GITPYTHON_IDENTITY_MISMATCH",
            "Git CLI and GitPython disagree on the repository identity.",
            status="MISMATCH",
        )
        gitpython = {
            "status": "PASS",
            "parity": True,
            "head_commit": gitpython_head,
            "head_tree": gitpython_tree,
            "worktree_dirty": gitpython_dirty,
            "read_only": True,
        }
    return {
        **base,
        "status": "PASS",
        "state": "ENABLED",
        "history_index_enabled": True,
        "git_executable_available": True,
        "repository_is_git": True,
        "head_commit": head.stdout.strip().lower(),
        "head_tree": tree.stdout.strip().lower(),
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "detached_head": branch.returncode != 0,
        "worktree_clean": not status_entries,
        "worktree_status_entry_count": len(status_entries),
        "gitpython": gitpython,
        "identity_scope": "HEAD_COMMIT_TREE_AND_LOCAL_WORKTREE_STATE",
        "remote_identity_included": False,
        "reason": (
            "Git history enrichment is available with explicit commit, tree, "
            "branch, and local worktree state."
        ),
    }
