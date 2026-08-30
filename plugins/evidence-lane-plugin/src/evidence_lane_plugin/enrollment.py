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
    resolve_git_executable,
    run_git,
    validate_remote_ref,
)
from .hashing import canonical_json_bytes, sha256_bytes
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
    branch_replacement_actor: str | None = None,
    dirty_local_authority_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Select one branch or apply one explicit clean fast-forward.

    A dirty governed checkout remains ineligible for fetch or merge. The sole
    exception is a source-byte-preserving authority replacement for the exact
    local checkout already on the requested branch and commit. That path is
    available only through an active task context and performs no Git write.
    """
    config = store.config(project_id)
    exact_branch = validate_remote_ref(branch, field="branch")
    branch_authorized = exact_branch in config.allowed_branches
    if not branch_authorized:
        require(
            bool(str(branch_replacement_actor or "").strip()),
            "PROJECT_SYNC_BRANCH_NOT_AUTHORIZED",
            "The selected Git branch is not in the registered authority. An "
            "active governed session and explicit one-branch replacement are "
            "required.",
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
    )
    before = identity_json(before_identity, config.repository_path)
    if not before_identity.is_clean:
        worktree_status = run_git(
            config.repository_path,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ).stdout
        status_sha256 = sha256_bytes(worktree_status.encode("utf-8"))
        explicit_authority_selection = bool(
            str(branch_replacement_actor or "").strip()
            and isinstance(dirty_local_authority_context, dict)
        )
        require(
            not branch_authorized or explicit_authority_selection,
            "WORKTREE_NOT_CLEAN",
            "Git fetch and fast-forward require a clean governed checkout; only "
            "an explicit byte-preserving local branch-authority selection may "
            "inspect a dirty checkout.",
            status="BLOCKED",
            worktree_status_sha256=status_sha256,
        )
        configured_repository = Path(config.repository_path).resolve()
        require(
            source_kind == "LOCAL_GIT_SOURCE"
            and Path(bounded_source).resolve() == configured_repository,
            "DIRTY_BRANCH_AUTHORITY_LOCAL_CHECKOUT_REQUIRED",
            "A dirty branch-authority-only replacement requires the exact "
            "registered local checkout as its source.",
            status="BLOCKED",
        )
        require(
            permitted_paths is not None
            and isinstance(dirty_local_authority_context, dict)
            and all(
                str(dirty_local_authority_context.get(field, "")).strip()
                for field in (
                    "session_id",
                    "task_id",
                    "task_class",
                    "lifecycle_state",
                    "task_contract_sha256",
                )
            ),
            "DIRTY_BRANCH_AUTHORITY_ACTIVE_TASK_REQUIRED",
            "A dirty branch-authority-only replacement requires one exact "
            "active governed task contract.",
            status="BLOCKED",
        )
        exact_expected = str(expected_commit or "").strip().lower()
        require(
            bool(_COMMIT_RE.fullmatch(exact_expected)),
            "DIRTY_BRANCH_AUTHORITY_EXPECTED_COMMIT_REQUIRED",
            "A dirty branch-authority-only replacement requires the exact "
            "current commit.",
            status="BLOCKED",
        )
        require(
            before_identity.commit_sha == exact_expected,
            "PROJECT_SYNC_EXPECTED_COMMIT_MISMATCH",
            "The governed checkout does not match the exact expected commit.",
            status="MISMATCH",
            expected_commit=exact_expected,
            checkout_commit=before_identity.commit_sha,
        )
        exact_dirty_context = dict(dirty_local_authority_context or {})
        selection_context: dict[str, Any] = {
            **exact_dirty_context,
            "selection_mode": "DIRTY_LOCAL_BRANCH_AUTHORITY_ONLY",
            "source_kind": source_kind,
            "expected_commit": exact_expected,
            "permitted_paths_sha256": sha256_bytes(
                canonical_json_bytes(permitted_paths)
            ),
            "worktree_status_sha256": status_sha256,
            "worktree_status_bytes": len(worktree_status.encode("utf-8")),
            "worktree_status_records": len(
                [entry for entry in worktree_status.split("\0") if entry]
            ),
            "dirty_worktree_preserved": True,
            "fetch_performed": False,
            "source_write_performed": False,
            "remote_write_performed": False,
            "merge_commit_created": False,
        }
        branch_authority = store.replace_branch_authority(
            project_id,
            branch=exact_branch,
            selected_by=str(branch_replacement_actor),
            repository=before,
            selection_context=selection_context,
        )
        after_identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
            expected_branch=exact_branch,
            expected_commit=exact_expected,
        )
        after = identity_json(after_identity, config.repository_path)
        after_status = run_git(
            config.repository_path,
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ).stdout
        require(
            before == after and worktree_status == after_status,
            "DIRTY_BRANCH_AUTHORITY_SOURCE_CHANGED",
            "The branch-authority-only replacement did not preserve the exact "
            "source and dirty worktree inventory.",
            status="MISMATCH",
        )
        return {
            "status": "PASS",
            "project_id": project_id,
            "operation": "DIRTY_LOCAL_BRANCH_AUTHORITY_ONLY",
            "source_kind": source_kind,
            "branch": exact_branch,
            "before": before,
            "after": after,
            "fetched_commit": exact_expected,
            "fast_forward_applied": False,
            "changed_paths": [],
            "branch_authority": branch_authority,
            "dirty_worktree_preserved": True,
            "worktree_status_sha256": status_sha256,
            "fetch_performed": False,
            "source_write_performed": False,
            "remote_write_performed": False,
            "merge_commit_created": False,
        }
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
    branch_authority = (
        store.replace_branch_authority(
            project_id,
            branch=exact_branch,
            selected_by=str(branch_replacement_actor),
            repository=after,
        )
        if not branch_authorized
        else {
            "status": "UNCHANGED",
            "project_id": project_id,
            "prior_allowed_branches": list(config.allowed_branches),
            "selected_branch": exact_branch,
            "authority_broadened": False,
            "receipt": None,
        }
    )
    return {
        "status": "PASS",
        "project_id": project_id,
        "operation": "CLEAN_FETCH_FAST_FORWARD_SYNC",
        "source_kind": source_kind,
        "branch": exact_branch,
        "before": before,
        "after": after,
        "fetched_commit": fetched_commit,
        "fast_forward_applied": fetched_commit != before_identity.commit_sha,
        "changed_paths": changed,
        "branch_authority": branch_authority,
        "dirty_worktree_preserved": False,
        "fetch_performed": True,
        "source_write_performed": fetched_commit != before_identity.commit_sha,
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
    capture_route: str = "GOVERNED_PROJECT_FULL",
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
            git_executable = resolve_git_executable(temporary)
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
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
            capture_route=capture_route,
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
