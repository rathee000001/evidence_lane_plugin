"""Create one exact App-authored branch commit through a GitHub App installation.

The command reconstructs the local commit through GitHub's Git Database REST
API, requires the object IDs to match the existing local commit exactly, and
fast-forwards the branch with ``force=false``. Private-key and installation-token
values remain in process memory and are never written to the receipt.

The local commit object is a deterministic preview only. Its author and
committer must both be the canonical Evidence Lane App bot; a human-authored
local commit followed by an App-authenticated push is rejected because pushing
credentials do not change commit authorship.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, overload

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.github_app_distribution import (
    EVIDENCE_LANE_APP_BOT_EMAIL,
    EVIDENCE_LANE_APP_BOT_NAME,
    ExactGitCommitPushRequest,
    GitCommitActor,
    GitHubAppExactCommitPushRoute,
    GitHubAppManifest,
    GitHubRESTInstallationTokenProvider,
    GitTreeChange,
    HttpxGitHubJSONTransport,
    InMemoryPEMGitHubAppJWTProvider,
    InstallationBinding,
    InstallationTokenBroker,
    InstallationTokenRequest,
    receipt_contains_secret,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

_ACTOR = re.compile(rb"^(author|committer) (.*) <([^<>]+)> ([0-9]+) ([+-][0-9]{4})$")


class ExactGitHubAppPushError(RuntimeError):
    """Raised before or during an exact GitHub App checkpoint push."""


@overload
def _git(
    repository_root: Path,
    *args: str,
    text: Literal[False] = False,
) -> bytes: ...


@overload
def _git(
    repository_root: Path,
    *args: str,
    text: Literal[True],
) -> str: ...


def _git(repository_root: Path, *args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=text,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise ExactGitHubAppPushError(
            f"git {' '.join(args)} failed: {str(stderr).strip()}"
        )
    return result.stdout


def _oid(repository_root: Path, revision: str) -> str:
    return str(_git(repository_root, "rev-parse", revision, text=True)).strip().lower()


def _actor(raw: bytes, *, expected_kind: bytes) -> GitCommitActor:
    match = _ACTOR.fullmatch(raw)
    if match is None or match.group(1) != expected_kind:
        raise ExactGitHubAppPushError(
            f"Local commit is missing one exact {expected_kind.decode()} identity."
        )
    name = match.group(2).decode("utf-8")
    email = match.group(3).decode("utf-8")
    timestamp = int(match.group(4).decode("ascii"))
    offset_text = match.group(5).decode("ascii")
    sign = 1 if offset_text[0] == "+" else -1
    offset = timezone(
        sign
        * timedelta(
            hours=int(offset_text[1:3]),
            minutes=int(offset_text[3:5]),
        )
    )
    date = datetime.fromtimestamp(timestamp, tz=offset).isoformat(timespec="seconds")
    return GitCommitActor.create(name=name, email=email, date=date)


def _commit_metadata(
    repository_root: Path,
    commit_sha: str,
) -> tuple[str, GitCommitActor, GitCommitActor]:
    raw = _git(repository_root, "cat-file", "commit", commit_sha)
    try:
        headers, message = raw.split(b"\n\n", 1)
        message_text = message.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExactGitHubAppPushError(
            "The exact local commit headers/message are not reproducible UTF-8."
        ) from exc
    header_lines = headers.splitlines()
    if any(line.startswith(b"gpgsig ") for line in header_lines):
        raise ExactGitHubAppPushError(
            "Signed local commits require a separately supplied signature route."
        )
    author_line = next(
        (line for line in header_lines if line.startswith(b"author ")), b""
    )
    committer_line = next(
        (line for line in header_lines if line.startswith(b"committer ")),
        b"",
    )
    return (
        message_text,
        _actor(author_line, expected_kind=b"author"),
        _actor(committer_line, expected_kind=b"committer"),
    )


def _require_app_bot_actor(
    author: GitCommitActor,
    committer: GitCommitActor,
) -> None:
    expected = (EVIDENCE_LANE_APP_BOT_NAME, EVIDENCE_LANE_APP_BOT_EMAIL)
    actual = (
        (author.name, author.email),
        (committer.name, committer.email),
    )
    if actual != (expected, expected):
        raise ExactGitHubAppPushError(
            "GITHUB_APP_BOT_ACTOR_REQUIRED: the exact local preview commit must "
            "use evidence-lane[bot] as both author and committer; an App push "
            "cannot repair human-authored commit metadata."
        )


def _tree_record(
    repository_root: Path, revision: str, path: str
) -> tuple[str, str] | None:
    raw = _git(repository_root, "ls-tree", "-z", revision, "--", path)
    if not raw:
        return None
    record = raw.rstrip(b"\0")
    try:
        metadata, exact_path = record.split(b"\t", 1)
        mode, object_type, object_sha = metadata.split(b" ", 2)
        decoded_path = exact_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExactGitHubAppPushError(
            f"The local Git tree record is not reproducible for {path!r}."
        ) from exc
    if decoded_path != path or object_type != b"blob":
        raise ExactGitHubAppPushError(
            f"The exact App route supports blob paths only: {path!r}."
        )
    return mode.decode("ascii"), object_sha.decode("ascii").lower()


def _tree_changes(
    repository_root: Path,
    *,
    parent_commit_sha: str,
    commit_sha: str,
) -> tuple[GitTreeChange, ...]:
    raw_paths = _git(
        repository_root,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "-z",
        parent_commit_sha,
        commit_sha,
    )
    try:
        paths = [value.decode("utf-8") for value in raw_paths.split(b"\0") if value]
    except UnicodeDecodeError as exc:
        raise ExactGitHubAppPushError(
            "The exact App route requires UTF-8 repository paths."
        ) from exc
    changes: list[GitTreeChange] = []
    for path in paths:
        current = _tree_record(repository_root, commit_sha, path)
        if current is None:
            parent = _tree_record(repository_root, parent_commit_sha, path)
            if parent is None:
                raise ExactGitHubAppPushError(
                    f"The deleted local Git path has no parent object: {path!r}."
                )
            changes.append(
                GitTreeChange.create(path=path, mode=parent[0], content=None)
            )
            continue
        content = _git(repository_root, "cat-file", "blob", current[1])
        change = GitTreeChange.create(path=path, mode=current[0], content=content)
        if change.blob_sha != current[1]:
            raise ExactGitHubAppPushError(
                f"The local blob identity changed while reading {path!r}."
            )
        changes.append(change)
    return tuple(changes)


def local_push_request(
    *,
    repository_root: Path,
    repository: str,
    branch: str,
    commit: str,
    project_id: str,
    task_id: str,
    request_id: str,
    idempotency_key: str,
) -> ExactGitCommitPushRequest:
    root = repository_root.resolve()
    commit_sha = _oid(root, f"{commit}^{{commit}}")
    parent_line = str(
        _git(root, "rev-list", "--parents", "-n", "1", commit_sha, text=True)
    ).strip()
    parent_fields = parent_line.split()
    if len(parent_fields) < 2 or parent_fields[0] != commit_sha:
        raise ExactGitHubAppPushError(
            "The exact App route requires a non-root commit with ordered parents."
        )
    parent_commit_sha = parent_fields[1]
    additional_parent_commit_shas = tuple(parent_fields[2:])
    expected_parent_tree_sha = _oid(root, f"{parent_commit_sha}^{{tree}}")
    expected_tree_sha = _oid(root, f"{commit_sha}^{{tree}}")
    message, author, committer = _commit_metadata(root, commit_sha)
    _require_app_bot_actor(author, committer)
    return ExactGitCommitPushRequest.create(
        request_id=request_id,
        idempotency_key=idempotency_key,
        project_id=project_id,
        task_id=task_id,
        repository=repository,
        branch=branch,
        expected_parent_commit_sha=parent_commit_sha,
        additional_parent_commit_shas=additional_parent_commit_shas,
        expected_parent_tree_sha=expected_parent_tree_sha,
        expected_tree_sha=expected_tree_sha,
        expected_commit_sha=commit_sha,
        commit_message=message,
        author=author,
        committer=committer,
        changes=_tree_changes(
            root,
            parent_commit_sha=parent_commit_sha,
            commit_sha=commit_sha,
        ),
    )


def execute_push(
    *,
    repository_root: Path,
    repository: str,
    branch: str,
    commit: str,
    project_id: str,
    task_id: str,
    accepted_pv: str,
    app_client_id: str,
    installation_id: str,
    private_key_pem: Path,
    request_id: str,
    idempotency_key: str,
    receipt_path: Path,
) -> dict[str, Any]:
    request = local_push_request(
        repository_root=repository_root,
        repository=repository,
        branch=branch,
        commit=commit,
        project_id=project_id,
        task_id=task_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    now = datetime.now(UTC)
    requested_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    token_expires = (
        (now + timedelta(minutes=50))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    binding_expires = (
        (now + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    permissions = {
        "metadata": "read",
        "actions": "read",
        "checks": "write",
        "contents": "write",
        "workflows": "write",
    }
    manifest = GitHubAppManifest.from_mapping(
        {
            "schema": "evidence-lane.github-app-manifest.v1",
            "app_slug": "evidence-lane",
            "manifest_version": "1",
            "repository_selection": "selected",
            "repository_permissions": permissions,
            "events": ["check_run", "push", "workflow_run"],
            "public": False,
        }
    )
    binding = InstallationBinding.create(
        binding_id=f"binding-{request_id}",
        manifest=manifest,
        installation_id=installation_id,
        project_id=project_id,
        task_id=task_id,
        accepted_pv=accepted_pv,
        repositories=[repository],
        permissions=permissions,
        expires_at=binding_expires,
    )
    jwt_provider = InMemoryPEMGitHubAppJWTProvider(
        client_id=app_client_id,
        private_key_pem=private_key_pem.resolve().read_bytes(),
    )
    transport = HttpxGitHubJSONTransport()
    try:
        token_provider = GitHubRESTInstallationTokenProvider(
            jwt_provider=jwt_provider,
            transport=transport,
        )
        broker = InstallationTokenBroker(
            manifest=manifest,
            binding=binding,
            provider=token_provider,
        )
        token_request = InstallationTokenRequest.create(
            request_id=f"token-{request_id}",
            idempotency_key=f"token-{idempotency_key}",
            project_id=project_id,
            task_id=task_id,
            installation_id=installation_id,
            repository=repository,
            permissions=permissions,
            requested_at=requested_at,
            expires_at=token_expires,
        )
        receipt = GitHubAppExactCommitPushRoute(
            broker=broker,
            transport=transport,
        ).execute(request, token_request=token_request, now=requested_at)
    finally:
        transport.close()
    if receipt_contains_secret(receipt):
        raise ExactGitHubAppPushError("The App push receipt contains a secret field.")
    output = receipt_path.resolve()
    content = canonical_json_bytes(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() != content:
        raise ExactGitHubAppPushError("Refusing to overwrite another App push receipt.")
    if not output.exists():
        output.write_bytes(content)
    return {
        "status": "PASS",
        "route": receipt["route"],
        "repository": receipt["repository"],
        "branch": receipt["branch"],
        "commit_sha": receipt["commit_sha"],
        "receipt": str(output),
        "receipt_sha256": receipt["receipt_sha256"],
        "file_sha256": sha256_bytes(content),
        "credentials_persisted": False,
        "force_push": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--accepted-pv", required=True)
    parser.add_argument("--app-client-id", required=True)
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--private-key-pem", type=Path, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = execute_push(
        repository_root=args.repository_root,
        repository=args.repository,
        branch=args.branch,
        commit=args.commit,
        project_id=args.project_id,
        task_id=args.task_id,
        accepted_pv=args.accepted_pv,
        app_client_id=args.app_client_id,
        installation_id=args.installation_id,
        private_key_pem=args.private_key_pem,
        request_id=args.request_id,
        idempotency_key=args.idempotency_key,
        receipt_path=args.receipt,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
