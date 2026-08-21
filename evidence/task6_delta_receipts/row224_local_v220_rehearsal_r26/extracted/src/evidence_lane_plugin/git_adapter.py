"""Fail-closed Git identity and bounded remote-write primitives."""

from __future__ import annotations

import json
import os
import re
import shutil

# Required for bounded Git argv; shell is never used.
import subprocess  # nosec B404
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .models import RepositoryIdentity

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True, slots=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def _git_executable() -> str:
    executable = shutil.which("git")
    require(
        bool(executable),
        "GIT_EXECUTABLE_NOT_FOUND",
        "Git is required for the governed code lane.",
        status="BLOCKED",
    )
    return str(executable)


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
    command = [_git_executable(), "-C", str(repo), *[str(arg) for arg in args]]
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
    # The executable is resolved locally and Git receives only list argv.
    completed = subprocess.run(  # nosec B603
        command,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=safe_env,
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    result = GitResult(
        args=tuple(str(arg) for arg in args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
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


def _tracked_and_untracked_paths(repository: Path) -> list[str]:
    result = run_git(
        repository,
        ["ls-files", "-co", "--exclude-standard", "-z"],
    )
    paths = [item.replace("\\", "/") for item in result.stdout.split("\0") if item]
    return sorted(set(paths))


def calculate_worktree_sha256(repository: str | Path) -> str:
    repo = Path(repository).resolve()
    members: list[dict[str, str | int]] = []
    for relative in _tracked_and_untracked_paths(repo):
        target = (repo / Path(relative)).resolve()
        try:
            target.relative_to(repo)
        except ValueError as exc:
            raise EvidenceLaneError(
                "REPOSITORY_PATH_ESCAPE",
                "A Git path escaped the selected repository.",
                status="BLOCKED",
                details={"path": relative},
            ) from exc
        if target.is_file() and not target.is_symlink():
            members.append(
                {
                    "path": relative,
                    "size": target.stat().st_size,
                    "sha256": sha256_file(target),
                }
            )
        elif target.is_symlink():
            members.append(
                {
                    "path": relative,
                    "size": len(os.readlink(target)),
                    "sha256": sha256_bytes(os.readlink(target).encode("utf-8")),
                }
            )
    return sha256_bytes(canonical_json_bytes(members))


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
        completed = subprocess.run(  # nosec B603
            [
                _git_executable(),
                "diff",
                "--no-index",
                "--binary",
                "--full-index",
                "--no-color",
                "--",
                os.devnull,
                str(target),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            close_fds=True,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        if completed.returncode not in (0, 1):
            raise EvidenceLaneError(
                "UNTRACKED_PATCH_FAILED",
                "Git could not create a patch for an untracked file.",
                details={"path": relative, "stderr": completed.stderr[-2000:]},
            )
        sections.append(completed.stdout)
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
