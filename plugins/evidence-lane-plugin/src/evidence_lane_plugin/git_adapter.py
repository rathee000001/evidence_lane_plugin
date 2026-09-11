"""Fail-closed Git identity and bounded remote-write primitives."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import time

# Git argv is direct; only the fixed, quoted Git credential/receiver protocols
# internally use Git's own shell support.
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from .bounded_io import (
    IOBudget,
    bounded_file_identity,
    run_bounded_process,
    run_bounded_process_digest,
    run_owned_bounded_process,
)
from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes

if TYPE_CHECKING:
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


def restoration_git(repository: Path, arguments: list[str], *, check: bool = True,
                    timeout_seconds: float = 30, max_stdout_bytes: int = 16 * 1024 * 1024) -> GitResult:
    """Fixed local restoration argv without inherited Git configuration or helpers.

    Callers supply only the operations below; this is not a public argv runner.
    Clones copy objects locally, disable templates/hooks, and never fetch a remote.
    """
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(('GIT_', 'GCM_'))}
    environment.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                        'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never',
                        'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
                        'GIT_LFS_SKIP_SMUDGE': '1'})
    command = [resolve_git_executable(repository), '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
               '-c', 'diff.external=', '-c', 'credential.helper=',
               '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
               '-C', str(repository), *arguments]
    completed = run_bounded_process(command, cwd=repository, env=environment,
        timeout_seconds=timeout_seconds, max_stdout_bytes=max_stdout_bytes, max_stderr_bytes=65536)
    result = GitResult(tuple(arguments), completed.returncode,
                       completed.stdout.decode('utf-8', errors='surrogateescape'),
                       completed.stderr.decode('utf-8', errors='replace'))
    require(not check or result.returncode == 0, 'GIT_RESTORATION_COMMAND_FAILED',
            'The selected local Git restoration operation failed.',
            returncode=result.returncode, operation=arguments[0])
    return result


def git_credential_provider(repository: Path) -> dict:
    """Identify an existing host GCM executable; never inspect credential values."""
    from .errors import LaneError
    from .storage import reject_links
    git = Path(resolve_git_executable(repository))
    name = 'git-credential-manager.exe' if os.name == 'nt' else 'git-credential-manager'
    directories = [git.parent, git.parent.parent / 'mingw64' / 'bin']
    directories += [Path(value) for value in os.get_exec_path() if value and Path(value).is_absolute()]
    for directory in directories:
        candidate = directory / name
        if not candidate.is_file():
            continue
        reject_links(candidate, Path(candidate.anchor))
        candidate = candidate.resolve(strict=True)
        if candidate.is_relative_to(repository.resolve()) or candidate.stat().st_size > 128 * 1024 * 1024:
            continue
        with candidate.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return {'provider': 'host_git_credential_manager', 'executable': str(candidate), 'sha256': digest,
                'credential_values_read': False, 'authentication_observed': False}
    raise LaneError('GIT_CREDENTIAL_PROVIDER_UNAVAILABLE', 'The selected host Git Credential Manager is unavailable.')


def git_ssh_provider(repository: Path) -> dict:
    """Bind the existing host OpenSSH agent route and public host-key database.

    No private key, agent identity, SSH configuration or credential is read.
    Availability is distinct from successful server or account authentication.
    """
    from .errors import LaneError
    from .storage import reject_links
    repository = repository.resolve()
    git = Path(resolve_git_executable(repository))
    name = 'ssh.exe' if os.name == 'nt' else 'ssh'
    directories = [Path(value) for value in os.get_exec_path() if value and Path(value).is_absolute()]
    if os.name == 'nt':
        directories.append(git.parent.parent / 'usr' / 'bin')
    executable = None
    for directory in directories:
        candidate = directory / name
        if not candidate.is_file():
            continue
        reject_links(candidate, Path(candidate.anchor))
        candidate = candidate.resolve(strict=True)
        if candidate.is_relative_to(repository) or candidate.stat().st_size > 128 * 1024 * 1024:
            continue
        executable = candidate
        break
    if executable is None:
        raise LaneError('GIT_SSH_PROVIDER_UNAVAILABLE', 'The selected host OpenSSH executable is unavailable.')
    known_hosts = Path.home() / '.ssh' / 'known_hosts'
    reject_links(known_hosts, Path(known_hosts.anchor))
    if (not known_hosts.is_file() or known_hosts.resolve().is_relative_to(repository)
            or not 0 < known_hosts.stat().st_size <= 4 * 1024 * 1024):
        raise LaneError('GIT_SSH_HOST_KEYS_REQUIRED', 'The host SSH route requires an existing bounded host-key database outside the source tree.')
    known_hosts = known_hosts.resolve(strict=True)
    agent = os.environ.get('SSH_AUTH_SOCK') or None
    # Native Windows OpenSSH uses its standard named pipe if no socket is set.
    native_windows = os.name == 'nt' and 'openssh' in {part.casefold() for part in executable.parts}
    if agent is None and not native_windows:
        raise LaneError('GIT_SSH_AGENT_REQUIRED', 'The selected host OpenSSH route requires an existing agent endpoint.')
    paths = [str(executable), str(known_hosts), agent or '']
    if any(len(value) > 2048 or any(ord(c) < 32 or c in '\"%$`' or ord(c) == 127 for c in value) for value in paths):
        raise LaneError('GIT_SSH_PROVIDER_PATH_INVALID', 'The host SSH provider paths require literal supported values.')
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'provider': 'host_openssh_agent', 'executable': str(executable), 'sha256': digest(executable),
        'known_hosts': str(known_hosts), 'known_hosts_sha256': digest(known_hosts),
        'agent_endpoint': agent, 'default_windows_agent': agent is None and native_windows,
        'credential_values_read': False, 'authentication_observed': False,
        'host_key_database_modified': False}


def _ssh_command(provider):
    """Fixed OpenSSH options; the host endpoint never provides shell commands."""
    command = [Path(provider['executable']).as_posix(), '-F', 'none', '-T']
    options = ['BatchMode=yes', 'NumberOfPasswordPrompts=0', 'PreferredAuthentications=publickey',
        'IdentityFile=none', 'CertificateFile=none', 'IdentitiesOnly=no', 'AddKeysToAgent=no',
        'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no', 'HostbasedAuthentication=no',
        'GSSAPIAuthentication=no', 'PKCS11Provider=none', 'SecurityKeyProvider=none',
        'StrictHostKeyChecking=yes', 'CheckHostIP=no', 'UpdateHostKeys=no', 'VerifyHostKeyDNS=no',
        'GlobalKnownHostsFile=none', 'KnownHostsCommand=none',
        'UserKnownHostsFile="' + Path(provider['known_hosts']).as_posix() + '"',
        'ForwardAgent=no', 'ForwardX11=no', 'ClearAllForwardings=yes', 'PermitLocalCommand=no',
        'LocalCommand=none', 'ProxyCommand=none', 'ProxyJump=none', 'RemoteCommand=none',
        'ControlMaster=no', 'ControlPath=none', 'ControlPersist=no', 'CanonicalizeHostname=no',
        'ConnectTimeout=15', 'ConnectionAttempts=1', 'ServerAliveInterval=15', 'ServerAliveCountMax=2']
    command += [item for option in options for item in ('-o', option)]
    return shlex.join(command)


def workflow_git(repository: Path, arguments: list[str], *, https: bool = False,
                 check: bool = True, tick=None, credential_provider: dict | None = None,
                 empty_clone: bool = False, ssh_provider: dict | None = None,
                 max_stdout_bytes: int = 4 * 1024 * 1024, deadline: float | None = None) -> GitResult:
    """Owned Git argv without hooks, filters, URL rewrites or inherited helpers.

    The workflow owners may bind an existing host GCM or OpenSSH agent route.
    Config that redirects transport or runs conversion is rejected.
    """
    from .errors import LaneError
    def remaining(maximum):
        value = maximum if deadline is None else min(maximum, deadline - time.monotonic())
        if value <= 0:
            raise LaneError('GIT_WORKFLOW_DEADLINE', 'The bounded Git observation deadline expired.')
        return value
    if tick:
        tick()
    if type(max_stdout_bytes) is not int or not 1 <= max_stdout_bytes <= 64 * 1024 * 1024 + 1:
        raise LaneError('GIT_WORKFLOW_OUTPUT_BUDGET', 'Select at most 64 MiB plus one byte for this internal Git output.')
    if empty_clone:
        from .storage import reject_links
        reject_links(repository, Path(repository.anchor))
        if arguments[0] != 'clone' or not repository.is_dir() or any(repository.iterdir()):
            raise LaneError('GIT_CLONE_TARGET_NOT_EMPTY', 'Clone requires the selected existing empty source folder.')
        configuration = ''
    else:
        configuration = restoration_git(repository, ['config', '--local', '--null', '--list', '--includes'],
            timeout_seconds=remaining(30), max_stdout_bytes=65_536 if deadline is not None else 16 * 1024 * 1024).stdout
    forbidden = ('include.', 'includeif.', 'url.', 'filter.', 'http.', 'https.',
                 'credential.', 'protocol.', 'remote.', 'uploadpack.', 'receive.')
    for record in filter(None, configuration.split('\0')):
        key = record.split('\n', 1)[0].lower()
        # Named URLs are compared to the caller's exact selection elsewhere;
        # refspecs cannot be used because every fetch passes its own one ref.
        if key.startswith('remote.') and key.endswith(('.url', '.pushurl', '.fetch')):
            continue
        if key.startswith('remote.') and key.endswith('.tagopt') and record.partition('\n')[2] == '--no-tags':
            continue
        if key.startswith(forbidden) or key in {'core.sshcommand', 'core.gitproxy', 'extensions.worktreeconfig'}:
            raise LaneError('GIT_WORKFLOW_CONFIG_UNSUPPORTED',
                'The selected Git configuration requires a separately qualified transport or filter route.')
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(('GIT_', 'GCM_', 'SSH_'))}
    environment.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'false', 'GIT_OPTIONAL_LOCKS': '0',
        'GIT_NO_LAZY_FETCH': '1', 'GIT_LFS_SKIP_SMUDGE': '1'})
    if empty_clone:
        # Do not discover a Git repository above the explicitly selected empty
        # destination. The clone itself creates its own independent metadata.
        environment['GIT_CEILING_DIRECTORIES'] = str(repository.parent)
    authentication = []
    if credential_provider is not None:
        if not https or git_credential_provider(repository) != credential_provider:
            raise LaneError('GIT_CREDENTIAL_PROVIDER_CHANGED', 'The prepared host credential provider changed or is incompatible.')
        # Git itself runs credential helpers through its shell. The only command
        # here is this exact host-resolved executable, with POSIX shell quoting;
        # neither repository config nor tool arguments supply executable text.
        helper = '!exec ' + shlex.quote(Path(credential_provider['executable']).as_posix())
        authentication = ['-c', 'credential.helper=' + helper, '-c', 'credential.interactive=false']
    if ssh_provider is not None:
        if https or credential_provider is not None or git_ssh_provider(repository) != ssh_provider:
            raise LaneError('GIT_SSH_PROVIDER_CHANGED', 'The prepared host SSH executable, agent endpoint or host keys changed.')
        environment.update(GIT_SSH_COMMAND=_ssh_command(ssh_provider), GIT_SSH_VARIANT='ssh', SSH_ASKPASS_REQUIRE='never')
        if ssh_provider['agent_endpoint'] is not None:
            environment['SSH_AUTH_SOCK'] = ssh_provider['agent_endpoint']
    command = [resolve_git_executable(repository), '-c', 'core.hooksPath=' + os.devnull,
        '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-c', 'diff.external=',
        '-c', 'credential.helper=', '-c', 'submodule.recurse=false', '-c', 'fetch.writeCommitGraph=false',
        '-c', 'fetch.fsckObjects=true', '-c', 'transfer.fsckObjects=true',
        '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
        '-c', 'protocol.https.allow=' + ('always' if https else 'never'),
        '-c', 'protocol.ssh.allow=' + ('always' if ssh_provider is not None else 'never'),
        '-c', 'http.followRedirects=false', '-c', 'http.sslVerify=true',
        '-c', 'core.autocrlf=false', '-c', 'core.symlinks=false',
        '-c', 'push.followTags=false', '-c', 'push.recurseSubmodules=no', *authentication,
        '-C', str(repository), *arguments]
    completed = run_owned_bounded_process(command, cwd=repository, env=environment,
        timeout_seconds=remaining(120), max_stdout_bytes=max_stdout_bytes, max_stderr_bytes=65536)
    if ssh_provider is not None and git_ssh_provider(repository) != ssh_provider:
        raise LaneError('GIT_SSH_PROVIDER_CHANGED', 'The SSH provider or host-key database changed during transport; reconcile the recorded effect.')
    result = GitResult(tuple(arguments), completed.returncode,
        completed.stdout.decode('utf-8', errors='surrogateescape'),
        completed.stderr.decode('utf-8', errors='replace'))
    if check and result.returncode:
        # Neither host paths nor provider output are an error-message channel.
        raise LaneError('GIT_WORKFLOW_COMMAND_FAILED', 'The selected bounded Git operation failed.',
            details={'operation': arguments[0], 'returncode': result.returncode})
    return result


def restoration_source_identity(repository: Path, *, tick=None) -> dict:
    """Bind HEAD, index/diffs and dirty bytes, excluding Git-ignored files."""
    from .storage import reject_links
    repo = repository.resolve(strict=True)
    reject_links(repo, Path(repo.anchor))
    top = restoration_git(repo, ['rev-parse', '--show-toplevel']).stdout.strip()
    require(Path(top).resolve() == repo, 'GIT_ROOT_REQUIRED', 'Select the exact Git worktree root.')
    head = restoration_git(repo, ['rev-parse', '--verify', 'HEAD']).stdout.strip()
    tree = restoration_git(repo, ['rev-parse', '--verify', 'HEAD^{tree}']).stdout.strip()
    require(bool(_SHA_RE.fullmatch(head)) and bool(_SHA_RE.fullmatch(tree)),
            'GIT_IDENTITY_INVALID', 'Git returned an invalid commit or tree.')
    paths = restoration_git(repo, ['ls-files', '-z', '--cached', '--others', '--exclude-standard']).stdout
    dirty = restoration_git(repo, ['ls-files', '-z', '--modified', '--deleted', '--others', '--exclude-standard']).stdout
    staged = restoration_git(repo, ['diff', '--cached', '--no-ext-diff', '--no-textconv', '--name-only', '-z']).stdout
    selected = sorted(set((dirty + '\0' + staged).split('\0')) - {''})
    budget = IOBudget(max_file_count=25000, max_aggregate_bytes=1024*1024*1024)
    rows = []
    for relative in selected:
        if tick:
            tick()
        raw = relative.encode('utf-8', errors='surrogateescape')
        rows.append(_dirty_path_content_identity(repo, relative, raw, budget=budget,
            staged=False, unstaged=False, untracked=False))
    status = restoration_git(repo, ['status', '--porcelain=v2', '-z', '--untracked-files=all']).stdout
    index = restoration_git(repo, ['ls-files', '--stage', '-z']).stdout
    diffs = [restoration_git(repo, ['diff', *extra, '--no-ext-diff', '--no-textconv', '--binary', '--full-index']).stdout
             for extra in ([], ['--cached'])]
    encode = lambda value: value.encode('utf-8', errors='surrogateescape')
    body = {'head': head, 'tree': tree,
            'path_set_sha256': sha256_bytes(encode(paths)).lower(),
            'index_sha256': sha256_bytes(encode(index)).lower(),
            'status_sha256': sha256_bytes(encode(status)).lower(),
            'diff_sha256': [sha256_bytes(encode(value)).lower() for value in diffs],
            'dirty_content_sha256': sha256_bytes(canonical_json_bytes(rows)).lower(),
            'dirty_path_count': len(rows), 'clean': not bool(status),
            'scope': 'git_index_dirty_and_untracked_excluding_ignored'}
    return {**body, 'digest': sha256_bytes(canonical_json_bytes(body)).lower()}


def restoration_selection(repository: Path, branch: str, commit: str, *, tick=None) -> dict:
    """Validate an existing local branch/commit and bounded, independent Git objects."""
    from .storage import reject_links
    require(bool(re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', commit)),
            'GIT_COMMIT_REQUIRED', 'Select a full lowercase Git commit identity.')
    require(bool(_SAFE_REF_RE.fullmatch(branch)) and not branch.startswith('-'),
            'GIT_BRANCH_REQUIRED', 'Select an exact existing local branch name.')
    restoration_git(repository, ['check-ref-format', '--branch', branch])
    branch_head = restoration_git(repository, ['rev-parse', '--verify', 'refs/heads/' + branch]).stdout.strip()
    resolved = restoration_git(repository, ['rev-parse', '--verify', commit + '^{commit}']).stdout.strip()
    require(resolved == commit, 'GIT_COMMIT_REQUIRED', 'Select a commit object, not an annotated tag identity.')
    restoration_git(repository, ['merge-base', '--is-ancestor', commit, branch_head])
    entries = restoration_git(repository, ['ls-tree', '-r', '-z', '-l', commit]).stdout.split('\0')
    file_count = source_bytes = gitlinks = 0
    for item in filter(None, entries):
        metadata, _ = item.split('\t', 1)
        mode, kind, _digest, size = metadata.split()
        file_count += 1
        if kind == 'blob':
            source_bytes += int(size)
            require(int(size) <= 64*1024*1024, 'GIT_RESTORE_FILE_BUDGET', 'A selected Git file exceeds 64 MiB.')
        elif kind == 'commit' and mode == '160000':
            gitlinks += 1
        else:
            require(False, 'GIT_TREE_INVALID', 'The selected commit contains an unsupported tree entry.')
    require(file_count <= 25000 and source_bytes <= 256*1024*1024,
            'GIT_RESTORE_TREE_BUDGET', 'Select a commit within 25,000 entries and 256 MiB of file data.')
    common = Path(restoration_git(repository, ['rev-parse', '--path-format=absolute', '--git-common-dir']).stdout.strip())
    reject_links(common, Path(common.anchor))
    require(common.is_absolute() and common.is_dir(), 'GIT_OBJECT_ROOT_REQUIRED', 'Git metadata must be an existing local directory.')
    objects = common / 'objects'
    reject_links(objects, common)
    require(not (objects / 'info' / 'alternates').exists(), 'GIT_ALTERNATE_OBJECTS_UNSUPPORTED',
            'Make the selected repository self-contained before restoring it.')
    budget = IOBudget(max_file_bytes=1024*1024*1024, max_file_count=25000, max_aggregate_bytes=1024*1024*1024)
    identity = []
    for folder, directories, files in os.walk(objects, followlinks=False):
        for name in sorted(directories):
            reject_links(Path(folder) / name, objects)
        for name in sorted(files):
            if tick:
                tick()
            path = Path(folder) / name
            file_identity = bounded_file_identity(path, budget=budget, root=objects)
            identity.append((path.relative_to(objects).as_posix(), file_identity['sha256'], file_identity['size_bytes']))
    tree = restoration_git(repository, ['rev-parse', '--verify', commit + '^{tree}']).stdout.strip()
    return {'branch_head': branch_head, 'commit': commit, 'tree': tree, 'entries': file_count,
            'source_bytes': source_bytes, 'gitlinks': gitlinks,
            'object_bytes': budget.consumed_bytes,
            'objects_digest': sha256_bytes(canonical_json_bytes(sorted(identity))).lower()}


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
    from .models import RepositoryIdentity
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
