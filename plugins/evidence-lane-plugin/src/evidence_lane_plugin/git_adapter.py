"""Fail-closed Git identity and bounded remote-write primitives."""

from __future__ import annotations

import json
import os
import re

# Required for bounded Git argv; shell is never used.
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .bounded_io import (
    IOBudget,
    bounded_file_identity,
    run_bounded_process,
    run_bounded_process_digest,
)
from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .models import RepositoryIdentity

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True, slots=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def resolve_git_executable(repository: str | Path | None = None) -> str:
    """Resolve Git only from an absolute PATH directory outside the repository."""

    forbidden = Path(repository).resolve() if repository is not None else None
    executable_name = "git.exe" if os.name == "nt" else "git"
    for raw_directory in os.get_exec_path():
        exact_directory = str(raw_directory or "").strip().strip('"')
        if not exact_directory:
            continue
        directory = Path(os.path.expandvars(exact_directory)).expanduser()
        if not directory.is_absolute():
            continue
        try:
            resolved = (directory / executable_name).resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_file() or resolved.is_symlink():
            continue
        if forbidden is not None and (
            resolved == forbidden or resolved.is_relative_to(forbidden)
        ):
            continue
        return str(resolved)
    raise EvidenceLaneError(
        "GIT_EXECUTABLE_NOT_FOUND",
        "Git is required from an absolute host PATH directory outside the governed repository.",
        status="BLOCKED",
    )


def try_resolve_git_executable(repository: str | Path | None = None) -> str | None:
    try:
        return resolve_git_executable(repository)
    except (EvidenceLaneError, OSError):
        return None


def _sanitize_remote(remote: str) -> str:
    value = remote.strip()
    if "://" in value:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, ""))
    if "@" in value and ":" in value:
        prefix, suffix = value.split("@", 1)
        if prefix and ":" not in prefix:
            return suffix
    return value


def _remote_hostname(remote: str) -> str:
    """Return the normalized network host for a Git remote, if one exists."""

    clean = _sanitize_remote(remote).strip()
    if "://" in clean:
        return (urlsplit(clean).hostname or "").casefold().rstrip(".")
    if ":" in clean and not re.match(r"^[A-Za-z]:[\\/]", clean):
        return clean.split(":", 1)[0].casefold().rstrip(".")
    return ""


def run_git(
    repository: str | Path,
    args: Iterable[str],
    *,
    check: bool = True,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> GitResult:
    repo = Path(repository).resolve()
    require(
        repo.is_dir(),
        "REPOSITORY_NOT_FOUND",
        "The selected repository directory does not exist.",
        status="MISMATCH",
        repository=str(repo),
    )
    command = [
        resolve_git_executable(repo),
        "-C",
        str(repo),
        *[str(arg) for arg in args],
    ]
    safe_env = os.environ.copy()
    safe_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_NOSYSTEM": safe_env.get("GIT_CONFIG_NOSYSTEM", "0"),
        }
    )
    if env:
        safe_env.update(env)
    completed = run_bounded_process(
        command,
        cwd=repo,
        env=safe_env,
        timeout_seconds=timeout,
    )
    result = GitResult(
        args=tuple(str(arg) for arg in args),
        returncode=completed.returncode,
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )
    if check and result.returncode != 0:
        raise EvidenceLaneError(
            "GIT_COMMAND_FAILED",
            "A bounded Git operation failed.",
            details={
                "args": list(result.args),
                "returncode": result.returncode,
                "stderr": result.stderr[-4000:],
            },
        )
    return result


def _parse_owner_name(remote: str, fallback_name: str) -> tuple[str, str]:
    clean = _sanitize_remote(remote).rstrip("/")
    path = clean
    if "://" in clean:
        path = urlsplit(clean).path
    elif ":" in clean and not re.match(r"^[A-Za-z]:[\\/]", clean):
        path = clean.split(":", 1)[1]
    parts = [part for part in re.split(r"[\\/]", path) if part]
    name = (parts[-1] if parts else fallback_name).removesuffix(".git")
    owner = parts[-2] if len(parts) >= 2 else "local"
    return owner, name


def _submodule_state(repository: Path) -> tuple[dict[str, str], ...]:
    result = run_git(
        repository,
        ["submodule", "status", "--recursive"],
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return ()
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        state = line[0]
        parts = line[1:].strip().split()
        if len(parts) >= 2:
            rows.append(
                {
                    "state": state,
                    "commit": parts[0],
                    "path": parts[1].replace("\\", "/"),
                }
            )
    return tuple(rows)


def calculate_worktree_sha256(repository: str | Path) -> str:
    """Compatibility name for the bounded Git-change identity.

    Historical full-tree receipts remain immutable. New receipts deliberately avoid
    re-reading clean tracked content and bind HEAD/tree, the complete path set, Git
    status/diffs, and every dirty or untracked byte instead.
    """

    return str(calculate_worktree_change_identity(repository)["working_identity_sha256"])


def _run_git_bytes(
    repository: Path,
    args: list[str],
    *,
    timeout: int = 120,
) -> bytes:
    """Return exact Git stdout bytes for identity hashing."""

    repo = repository.resolve()
    require(
        repo.is_dir(),
        "REPOSITORY_NOT_FOUND",
        "The selected repository directory does not exist.",
        status="MISMATCH",
        repository=str(repo),
    )
    command = [resolve_git_executable(repo), "-C", str(repo), *args]
    safe_env = os.environ.copy()
    safe_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_NOSYSTEM": safe_env.get("GIT_CONFIG_NOSYSTEM", "0"),
        }
    )
    completed = run_bounded_process(
        command,
        cwd=repo,
        env=safe_env,
        timeout_seconds=timeout,
    )
    if completed.returncode != 0:
        raise EvidenceLaneError(
            "GIT_COMMAND_FAILED",
            "A bounded Git identity operation failed.",
            details={
                "args": args,
                "returncode": completed.returncode,
                "stderr": completed.stderr[-4000:].decode("utf-8", errors="replace"),
            },
        )
    return completed.stdout


def _zero_delimited_path_bytes(repository: Path, args: list[str]) -> set[bytes]:
    return {value for value in _run_git_bytes(repository, args).split(b"\0") if value}


def _path_set_sha256(paths: set[bytes]) -> str:
    payload = b"\0".join(sorted(paths))
    if payload:
        payload += b"\0"
    return sha256_bytes(payload)


def run_git_digest(
    repository: Path,
    args: list[str],
    *,
    timeout: int = 120,
) -> tuple[str, int]:
    repo = repository.resolve()
    command = [resolve_git_executable(repo), "-C", str(repo), *args]
    safe_env = os.environ.copy()
    safe_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_NOSYSTEM": safe_env.get("GIT_CONFIG_NOSYSTEM", "0"),
        }
    )
    completed = run_bounded_process_digest(
        command,
        cwd=repo,
        env=safe_env,
        timeout_seconds=timeout,
    )
    if completed.returncode != 0:
        raise EvidenceLaneError(
            "GIT_COMMAND_FAILED",
            "A bounded Git identity digest operation failed.",
            details={
                "args": args,
                "returncode": completed.returncode,
                "stderr": completed.stderr[-4000:].decode(
                    "utf-8", errors="replace"
                ),
            },
        )
    return completed.stdout_sha256, completed.stdout_bytes


def _dirty_path_content_identity(
    repository: Path,
    relative: str,
    relative_bytes: bytes,
    *,
    budget: IOBudget,
    staged: bool,
    unstaged: bool,
    untracked: bool,
) -> dict[str, Any]:
    """Hash one dirty path without following a symlink outside the repository."""

    lexical_target = Path(os.path.abspath(repository / Path(relative)))
    try:
        lexical_target.relative_to(repository)
    except ValueError as exc:
        raise EvidenceLaneError(
            "REPOSITORY_PATH_ESCAPE",
            "A dirty Git path escaped the selected repository.",
            status="BLOCKED",
            details={"path_sha256": sha256_bytes(relative.encode("utf-8"))},
        ) from exc

    target = repository / Path(relative)
    if target.is_symlink():
        link_value = os.readlink(target)
        content = link_value.encode("utf-8")
        content_kind = "SYMLINK_TARGET"
        size_bytes: int | None = len(content)
        content_sha256: str | None = sha256_bytes(content)
    elif target.is_file():
        content_kind = "REGULAR_FILE"
        bounded = bounded_file_identity(target, budget=budget, root=repository)
        size_bytes = int(bounded["size_bytes"])
        content_sha256 = str(bounded["sha256"])
    elif not target.exists():
        content_kind = "DELETION_PURGE_RECEIPT"
        size_bytes = None
        content_sha256 = None
    else:
        content_kind = "NON_REGULAR_PATH"
        size_bytes = None
        content_sha256 = None

    return {
        "path_sha256": sha256_bytes(relative_bytes),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "content_kind": content_kind,
        "size_bytes": size_bytes,
        "content_sha256": content_sha256,
    }


def calculate_worktree_change_identity(repository: str | Path) -> dict[str, Any]:
    """Seal the complete Git/index/dirty-byte identity without persisting raw paths."""

    repo = Path(repository).resolve()
    identity = inspect_repository(repo)
    complete_path_bytes = _zero_delimited_path_bytes(
        repo,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
    )
    staged_path_bytes = _zero_delimited_path_bytes(
        repo,
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRD", "--", "."],
    )
    unstaged_path_bytes = _zero_delimited_path_bytes(
        repo,
        ["diff", "--name-only", "-z", "--diff-filter=ACMRD", "--", "."],
    )
    untracked_path_bytes = _zero_delimited_path_bytes(
        repo,
        ["ls-files", "--others", "--exclude-standard", "-z", "--", "."],
    )
    dirty_path_bytes = sorted(
        staged_path_bytes | unstaged_path_bytes | untracked_path_bytes
    )
    dirty_content_budget = IOBudget()
    content_identities = [
        _dirty_path_content_identity(
            repo,
            os.fsdecode(relative_bytes).replace("\\", "/"),
            relative_bytes,
            budget=dirty_content_budget,
            staged=relative_bytes in staged_path_bytes,
            unstaged=relative_bytes in unstaged_path_bytes,
            untracked=relative_bytes in untracked_path_bytes,
        )
        for relative_bytes in dirty_path_bytes
    ]
    tracked_content_identities = [
        row for row in content_identities if not bool(row["untracked"])
    ]
    untracked_content_identities = [
        row for row in content_identities if bool(row["untracked"])
    ]

    status_v2 = _run_git_bytes(
        repo, ["status", "--porcelain=v2", "-z", "--untracked-files=all"]
    )
    cached_diff_sha256, cached_diff_bytes = run_git_digest(
        repo, ["diff", "--cached", "--binary", "--full-index", "--", "."]
    )
    unstaged_diff_sha256, unstaged_diff_bytes = run_git_digest(
        repo, ["diff", "--binary", "--full-index", "--", "."]
    )
    tracked_head_diff_sha256, tracked_head_diff_bytes = run_git_digest(
        repo, ["diff", "HEAD", "--binary", "--full-index", "--", "."]
    )

    body = {
        "schema": "evidence-lane.git-worktree-change-identity.v1",
        "branch": identity.branch,
        "head": identity.commit_sha,
        "tree": identity.tree_sha,
        "complete_path_set_sha256": _path_set_sha256(complete_path_bytes),
        "path_count": len(complete_path_bytes),
        "status_sha256": sha256_bytes(status_v2),
        "cached_diff_sha256": cached_diff_sha256,
        "unstaged_diff_sha256": unstaged_diff_sha256,
        "tracked_head_diff_sha256": tracked_head_diff_sha256,
        "cached_diff_bytes": cached_diff_bytes,
        "unstaged_diff_bytes": unstaged_diff_bytes,
        "tracked_head_diff_bytes": tracked_head_diff_bytes,
        "dirty_path_set_sha256": _path_set_sha256(set(dirty_path_bytes)),
        "dirty_path_count": len(dirty_path_bytes),
        "tracked_dirty_path_count": len(staged_path_bytes | unstaged_path_bytes),
        "staged_path_count": len(staged_path_bytes),
        "unstaged_path_count": len(unstaged_path_bytes),
        "untracked_path_count": len(untracked_path_bytes),
        "tracked_dirty_content_sha256": sha256_bytes(
            canonical_json_bytes(tracked_content_identities)
        ),
        "untracked_content_sha256": sha256_bytes(
            canonical_json_bytes(untracked_content_identities)
        ),
        "dirty_content_sha256": sha256_bytes(
            canonical_json_bytes(content_identities)
        ),
        "content_identity_count": len(content_identities),
        "clean_member_content_rehashed": False,
        "legacy_full_tree_hash_compatibility": (
            "PRIOR_RECEIPTS_REMAIN_IMMUTABLE"
        ),
        "dirty_content_budget": dirty_content_budget.receipt(),
        "raw_paths_persisted": False,
        "ignored_paths_included": False,
    }
    return {
        **body,
        "working_identity_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def inspect_repository(
    repository: str | Path,
    *,
    expected_owner: str | None = None,
    expected_name: str | None = None,
    expected_branch: str | None = None,
    expected_commit: str | None = None,
    require_clean: bool = False,
) -> RepositoryIdentity:
    repo = Path(repository).resolve()
    inside = run_git(repo, ["rev-parse", "--is-inside-work-tree"]).stdout.strip()
    require(
        inside == "true",
        "NOT_A_GIT_WORKTREE",
        "The selected directory is not a Git working tree.",
        status="MISMATCH",
    )
    commit = run_git(repo, ["rev-parse", "HEAD"]).stdout.strip().lower()
    tree = run_git(repo, ["rev-parse", "HEAD^{tree}"]).stdout.strip().lower()
    require(
        bool(_SHA_RE.fullmatch(commit)) and bool(_SHA_RE.fullmatch(tree)),
        "GIT_IDENTITY_INVALID",
        "Git returned an invalid commit or tree identity.",
        status="MISMATCH",
    )
    branch_result = run_git(
        repo, ["symbolic-ref", "--short", "-q", "HEAD"], check=False
    )
    branch = branch_result.stdout.strip() or "DETACHED"
    remote_result = run_git(repo, ["config", "--get", "remote.origin.url"], check=False)
    remote = (
        _sanitize_remote(remote_result.stdout.strip())
        if remote_result.returncode == 0
        else ""
    )
    remote = remote or f"local://{repo.as_posix()}"
    owner, name = _parse_owner_name(remote, repo.name)
    status = run_git(repo, ["status", "--porcelain=v1", "--untracked-files=all"]).stdout
    is_clean = not bool(status.strip())
    identity = RepositoryIdentity(
        provider="github" if _remote_hostname(remote) == "github.com" else "git",
        repository_url=remote,
        owner=owner,
        name=name,
        branch=branch,
        commit_sha=commit,
        tree_sha=tree,
        is_clean=is_clean,
        submodules=_submodule_state(repo),
        lfs_state=(
            "CONFIGURED"
            if (repo / ".gitattributes").is_file()
            and "filter=lfs"
            in (repo / ".gitattributes").read_text(encoding="utf-8", errors="ignore")
            else "NOT_USED"
        ),
    )
    expected = {
        "owner": (expected_owner, identity.owner),
        "name": (expected_name, identity.name),
        "branch": (expected_branch, identity.branch),
        "commit": (
            expected_commit.lower() if expected_commit else None,
            identity.commit_sha,
        ),
    }
    mismatches = {
        key: {"expected": wanted, "actual": actual}
        for key, (wanted, actual) in expected.items()
        if wanted is not None and wanted != actual
    }
    require(
        not mismatches,
        "REPOSITORY_IDENTITY_MISMATCH",
        "The selected repository does not match the authorized identity.",
        status="MISMATCH",
        mismatches=mismatches,
    )
    require(
        not require_clean or identity.is_clean,
        "WORKTREE_NOT_CLEAN",
        "The entry build requires a clean Git working tree.",
        status="BLOCKED",
        porcelain=status[:4000],
    )
    return identity


def diff_patch(repository: str | Path) -> str:
    repo = Path(repository).resolve()
    tracked = run_git(
        repo,
        [
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-color",
            "HEAD",
            "--",
        ],
    ).stdout
    untracked = run_git(
        repo,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ).stdout.split("\0")
    sections = [tracked] if tracked else []
    for relative in sorted(path for path in untracked if path):
        target = (repo / relative).resolve()
        if not target.is_file():
            continue
        # The command is fixed; the repository-relative path was bounded above.
        completed = run_bounded_process(
            [
                resolve_git_executable(repo),
                "diff",
                "--no-index",
                "--binary",
                "--full-index",
                "--no-color",
                "--",
                os.devnull,
                str(target),
            ],
            cwd=repo,
            timeout_seconds=60,
        )
        if completed.returncode not in (0, 1):
            raise EvidenceLaneError(
                "UNTRACKED_PATCH_FAILED",
                "Git could not create a patch for an untracked file.",
                details={
                    "path": relative,
                    "stderr": completed.stderr[-2000:].decode(
                        "utf-8", errors="replace"
                    ),
                },
            )
        sections.append(completed.stdout.decode("utf-8", errors="replace"))
    return "\n".join(section.rstrip() for section in sections if section).rstrip() + (
        "\n" if sections else ""
    )


def validate_remote_ref(value: str, *, field: str) -> str:
    require(
        bool(value) and len(value) <= 240 and bool(_SAFE_REF_RE.fullmatch(value)),
        "UNSAFE_GIT_REF",
        f"The {field} contains unsupported characters.",
        status="BLOCKED",
        field=field,
    )
    require(
        not value.startswith(("-", "/", "."))
        and ".." not in value
        and "@{" not in value,
        "UNSAFE_GIT_REF",
        f"The {field} is not a safe Git ref.",
        status="BLOCKED",
        field=field,
    )
    return value


def resolve_local_ref_identity(
    repository: str | Path,
    *,
    local_ref: str,
) -> tuple[str, str]:
    """Resolve one safe local ref to immutable commit and tree identities."""

    safe_local = validate_remote_ref(local_ref, field="local_ref")
    commit_result = run_git(
        repository,
        ["rev-parse", "--verify", f"{safe_local}^{{commit}}"],
    )
    commit = commit_result.stdout.strip().lower()
    require(
        commit_result.returncode == 0
        and len(commit) in {40, 64}
        and bool(_SHA_RE.fullmatch(commit)),
        "REMOTE_LOCAL_REF_UNRESOLVED",
        "The prepared local Git ref does not resolve to one exact commit.",
        status="BLOCKED",
        local_ref=safe_local,
    )
    tree_result = run_git(
        repository,
        ["rev-parse", "--verify", f"{commit}^{{tree}}"],
    )
    tree = tree_result.stdout.strip().lower()
    require(
        tree_result.returncode == 0
        and len(tree) in {40, 64}
        and bool(_SHA_RE.fullmatch(tree)),
        "REMOTE_LOCAL_TREE_UNRESOLVED",
        "The prepared local Git commit does not resolve to one exact tree.",
        status="BLOCKED",
        local_commit=commit,
    )
    return commit, tree


def resolve_named_remote_identity(
    repository: str | Path,
    *,
    remote: str,
    expected_owner: str,
    expected_name: str,
) -> dict[str, Any]:
    """Resolve one configured remote without retaining credentials or URL text."""

    safe_remote = validate_remote_ref(remote, field="remote")
    result = run_git(
        repository,
        ["config", "--get", f"remote.{safe_remote}.url"],
        check=False,
    )
    sanitized_url = _sanitize_remote(result.stdout.strip())
    require(
        result.returncode == 0 and bool(sanitized_url),
        "REMOTE_GIT_NAMED_REMOTE_NOT_CONFIGURED",
        "The selected Git remote is not configured in the governed repository.",
        status="MISMATCH",
        remote=safe_remote,
    )
    owner, name = _parse_owner_name(sanitized_url, Path(repository).resolve().name)
    require(
        owner == expected_owner and name == expected_name,
        "REMOTE_REPOSITORY_IDENTITY_MISMATCH",
        "The selected Git remote does not match the governed repository owner/name.",
        status="MISMATCH",
        remote=safe_remote,
        expected_owner=expected_owner,
        expected_name=expected_name,
        observed_owner=owner,
        observed_name=name,
    )
    hostname = _remote_hostname(sanitized_url)
    return {
        "remote_name": safe_remote,
        "provider": "github" if hostname == "github.com" else "git",
        "hostname": hostname or "LOCAL_OR_UNSPECIFIED",
        "owner": owner,
        "name": name,
        "sanitized_url_sha256": sha256_bytes(sanitized_url.encode("utf-8")),
        "credential_requested_or_stored": False,
    }


def remote_push(
    repository: str | Path,
    *,
    remote: str,
    local_ref: str,
    remote_ref: str,
) -> GitResult:
    safe_remote = validate_remote_ref(remote, field="remote")
    safe_local = validate_remote_ref(local_ref, field="local_ref")
    safe_target = validate_remote_ref(remote_ref, field="remote_ref")
    return run_git(
        repository,
        [
            "push",
            "--porcelain",
            safe_remote,
            f"{safe_local}:refs/heads/{safe_target}",
        ],
        timeout=300,
    )


def identity_json(
    identity: RepositoryIdentity, repository: str | Path
) -> dict[str, object]:
    payload = identity.as_dict()
    payload["submodules"] = list(identity.submodules)
    payload["worktree_sha256"] = calculate_worktree_sha256(repository)
    return json.loads(json.dumps(payload, sort_keys=True))
