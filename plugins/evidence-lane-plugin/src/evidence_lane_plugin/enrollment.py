"""Bounded local adoption or read-only remote clone for project enrollment."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import require
from .git_adapter import (
    identity_json,
    inspect_repository,
    run_git,
    validate_remote_ref,
)
from .models import ProjectConfig
from .store import ProjectStore

_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _bounded_source(source: str) -> tuple[str, str]:
    source_path = Path(source)
    if source_path.is_dir():
        return str(source_path.resolve()), "LOCAL_GIT_SOURCE"
    parsed = urlparse(source)
    require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment,
        "PROJECT_SYNC_SOURCE_INVALID",
        "Git sync accepts only an exact local Git path or an HTTPS Git URL "
        "without embedded credentials, query parameters, or fragments.",
        status="BLOCKED",
    )
    return source, "CREDENTIAL_FREE_HTTPS"


def _path_is_permitted(path: str, permitted_paths: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for raw_pattern in permitted_paths:
        pattern = raw_pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        if pattern.endswith("/") and normalized.startswith(pattern):
            return True
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
        if normalized == pattern:
            return True
    return False


def sync_selected_branch(
    store: ProjectStore,
    *,
    project_id: str,
    source: str,
    branch: str,
    expected_commit: str | None = None,
    permitted_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch one explicit source/branch and apply only a clean fast-forward."""
    config = store.config(project_id)
    exact_branch = validate_remote_ref(branch, field="branch")
    require(
        exact_branch in config.allowed_branches,
        "PROJECT_SYNC_BRANCH_NOT_AUTHORIZED",
        "The selected Git branch is not in the registered authority.",
        status="BLOCKED",
        branch=exact_branch,
        allowed_branches=config.allowed_branches,
    )
    bounded_source, source_kind = _bounded_source(source)
    if source_kind == "LOCAL_GIT_SOURCE":
        inspect_repository(
            bounded_source,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
            expected_branch=exact_branch,
        )
    before_identity = inspect_repository(
        config.repository_path,
        expected_owner=config.expected_owner,
        expected_name=config.expected_name,
        expected_branch=exact_branch,
        require_clean=True,
    )
    before = identity_json(before_identity, config.repository_path)
    run_git(
        config.repository_path,
        ["fetch", "--no-tags", "--", bounded_source, f"refs/heads/{exact_branch}"],
        timeout=300,
    )
    fetched_commit = (
        run_git(
            config.repository_path,
            ["rev-parse", "FETCH_HEAD"],
        )
        .stdout.strip()
        .lower()
    )
    require(
        bool(_COMMIT_RE.fullmatch(fetched_commit)),
        "PROJECT_SYNC_FETCHED_COMMIT_INVALID",
        "Git returned an invalid fetched commit.",
        status="MISMATCH",
    )
    if expected_commit:
        require(
            fetched_commit == expected_commit.strip().lower(),
            "PROJECT_SYNC_EXPECTED_COMMIT_MISMATCH",
            "The selected source branch did not resolve to the expected commit.",
            status="MISMATCH",
            expected_commit=expected_commit.strip().lower(),
            fetched_commit=fetched_commit,
        )
    ancestor = run_git(
        config.repository_path,
        ["merge-base", "--is-ancestor", before_identity.commit_sha, fetched_commit],
        check=False,
    )
    require(
        ancestor.returncode == 0,
        "PROJECT_SYNC_NOT_FAST_FORWARD",
        "The selected Git source is not a fast-forward from the governed checkout.",
        status="BLOCKED",
        before_commit=before_identity.commit_sha,
        fetched_commit=fetched_commit,
    )
    changed = [
        path.replace("\\", "/")
        for path in run_git(
            config.repository_path,
            [
                "diff",
                "--name-only",
                "-z",
                before_identity.commit_sha,
                fetched_commit,
                "--",
            ],
        ).stdout.split("\0")
        if path
    ]
    if permitted_paths is not None:
        blocked = [
            path for path in changed if not _path_is_permitted(path, permitted_paths)
        ]
        require(
            not blocked,
            "PROJECT_SYNC_PATH_OUTSIDE_TASK",
            "The selected fast-forward changes paths outside the active bounded task.",
            status="BLOCKED",
            blocked_paths=blocked,
            permitted_paths=permitted_paths,
        )
    if fetched_commit != before_identity.commit_sha:
        run_git(
            config.repository_path,
            ["merge", "--ff-only", "--no-edit", fetched_commit],
            timeout=300,
        )
    after_identity = inspect_repository(
        config.repository_path,
        expected_owner=config.expected_owner,
        expected_name=config.expected_name,
        expected_branch=exact_branch,
        require_clean=True,
    )
    after = identity_json(after_identity, config.repository_path)
    return {
        "status": "PASS",
        "project_id": project_id,
        "source_kind": source_kind,
        "branch": exact_branch,
        "before": before,
        "after": after,
        "fetched_commit": fetched_commit,
        "fast_forward_applied": fetched_commit != before_identity.commit_sha,
        "changed_paths": changed,
        "remote_write_performed": False,
        "merge_commit_created": False,
    }


def enroll_project(
    store: ProjectStore,
    *,
    project_id: str,
    display_name: str,
    source: str,
    expected_owner: str,
    expected_name: str,
    branch: str,
    sensitivity: str,
) -> dict[str, Any]:
    """Adopt an exact local path or clone HTTPS source without remote mutation."""
    store.validate_project_id(project_id)
    source_path = Path(source)
    cloned = False
    clone_source: str | None = None
    if source_path.is_dir():
        repository = source_path.resolve()
        enrollment_mode = "ADOPTED_LOCAL_PATH"
    else:
        parsed = urlparse(source)
        require(
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment,
            "PROJECT_ENROLL_SOURCE_INVALID",
            "Remote enrollment accepts only an HTTPS Git URL without embedded "
            "credentials, query parameters, or fragments.",
            status="BLOCKED",
        )
        workspaces = store.root / "workspaces"
        workspaces.mkdir(parents=True, exist_ok=True)
        repository = (workspaces / project_id).resolve()
        repository.relative_to(store.root)
        require(
            not repository.exists(),
            "PROJECT_ENROLL_WORKSPACE_EXISTS",
            "The governed clone target already exists; enrollment refuses to "
            "overwrite or silently adopt it.",
            status="BLOCKED",
            project_id=project_id,
        )
        temporary = Path(
            tempfile.mkdtemp(prefix=f".enroll-{project_id}-", dir=workspaces)
        ).resolve()
        try:
            git_executable = shutil.which("git")
            require(
                bool(git_executable),
                "PROJECT_ENROLL_GIT_UNAVAILABLE",
                "Git is required for HTTPS project enrollment.",
                status="BLOCKED",
            )
            completed = subprocess.run(  # nosec B603
                [
                    str(git_executable),
                    "clone",
                    "--no-tags",
                    "--single-branch",
                    "--branch",
                    branch,
                    "--",
                    source,
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
            require(
                completed.returncode == 0,
                "PROJECT_ENROLL_CLONE_FAILED",
                "Git could not clone the exact enrollment source.",
                status="FAIL",
                returncode=completed.returncode,
                stderr_tail=completed.stderr[-2000:],
            )
            inspect_repository(
                temporary,
                expected_owner=expected_owner,
                expected_name=expected_name,
            )
            os.replace(temporary, repository)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        cloned = True
        clone_source = source
        enrollment_mode = "CLONED_HTTPS_SOURCE"
    identity = inspect_repository(
        repository,
        expected_owner=expected_owner,
        expected_name=expected_name,
    )
    require(
        identity.branch == branch,
        "PROJECT_ENROLL_BRANCH_MISMATCH",
        "The enrolled repository is not on the exact authorized branch.",
        status="MISMATCH",
        expected_branch=branch,
        actual_branch=identity.branch,
    )
    registered = store.register_project(
        ProjectConfig(
            project_id=project_id,
            display_name=display_name,
            repository_path=str(repository),
            expected_owner=expected_owner,
            expected_name=expected_name,
            allowed_branches=[branch],
            source_lane=(
                "local_code"
                if enrollment_mode == "ADOPTED_LOCAL_PATH"
                else "github_code"
            ),
            persistence_mode="governed_by_host",
            sensitivity=sensitivity.upper(),
        )
    )
    return {
        "status": "PASS",
        "enrollment_mode": enrollment_mode,
        "project_id": project_id,
        "repository_path": str(repository),
        "clone_source": clone_source,
        "remote_write_performed": False,
        "existing_lineage_overwritten": False,
        "cloned": cloned,
        "project": registered,
    }
