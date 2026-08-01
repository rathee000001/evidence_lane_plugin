"""Truthful optional Git arm for source intake and history enrichment."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require

GIT_ARM_MODES = ("AUTO", "REQUIRED", "DISABLED")


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
            "git_executable_available": bool(shutil.which("git")),
            "repository_is_git": None,
            "reason": "The user disabled Git enrichment for this intake.",
        }

    executable = shutil.which("git")
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

    completed = subprocess.run(  # nosec B603
        [str(executable), "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        close_fds=True,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    inside = completed.returncode == 0 and completed.stdout.strip() == "true"
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

    head = subprocess.run(  # nosec B603
        [str(executable), "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        close_fds=True,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
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
    return {
        **base,
        "status": "PASS",
        "state": "ENABLED",
        "history_index_enabled": True,
        "git_executable_available": True,
        "repository_is_git": True,
        "head_commit": head.stdout.strip().lower(),
        "reason": "Git history enrichment is available and selected.",
    }
