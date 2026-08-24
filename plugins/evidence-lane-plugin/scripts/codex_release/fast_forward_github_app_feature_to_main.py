"""Fast-forward ``main`` to one exact green feature commit through the GitHub App.

The command never checks out, edits, or directly pushes ``main``. GitHub's
current route verifies the exact source/target refs, strict ancestry, required
exact-head workflows, App bot actor, and final ``main`` ref before and after one
non-force ref update.
Credentials remain in process memory and are never written to the receipt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.github_app_distribution import (
    GitHubAppMainFastForwardRequest,
    GitHubAppMainFastForwardRoute,
    GitHubAppManifest,
    GitHubRESTInstallationTokenProvider,
    HttpxGitHubJSONTransport,
    InMemoryPEMGitHubAppJWTProvider,
    InstallationBinding,
    InstallationTokenBroker,
    InstallationTokenRequest,
    receipt_contains_secret,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes


class GitHubAppMainFastForwardError(RuntimeError):
    """Raised before or during the governed feature-to-main fast-forward."""


def _git(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        raise GitHubAppMainFastForwardError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip().lower()


def local_main_fast_forward_request(
    *,
    repository_root: Path,
    repository: str,
    source_branch: str,
    target_branch: str,
    source_commit: str,
    expected_target_commit_sha: str,
    required_workflow_names: tuple[str, ...],
    project_id: str,
    task_id: str,
    request_id: str,
    idempotency_key: str,
) -> GitHubAppMainFastForwardRequest:
    root = repository_root.resolve()
    exact_source_commit = _git(root, "rev-parse", f"{source_commit}^{{commit}}")
    exact_source_tree = _git(root, "rev-parse", f"{exact_source_commit}^{{tree}}")
    current_branch = _git(root, "branch", "--show-current")
    if current_branch != source_branch:
        raise GitHubAppMainFastForwardError(
            "GITHUB_APP_MAIN_FAST_FORWARD_WRONG_LOCAL_BRANCH: the invoking worktree "
            "must remain attached to the exact governed feature branch."
        )
    if target_branch != "main" or source_branch == target_branch:
        raise GitHubAppMainFastForwardError(
            "GITHUB_APP_MAIN_FAST_FORWARD_BOUNDARY_INVALID: only an exact non-main "
            "feature source may fast-forward main."
        )
    return GitHubAppMainFastForwardRequest.create(
        request_id=request_id,
        idempotency_key=idempotency_key,
        project_id=project_id,
        task_id=task_id,
        repository=repository,
        source_branch=source_branch,
        target_branch=target_branch,
        expected_source_commit_sha=exact_source_commit,
        expected_source_tree_sha=exact_source_tree,
        expected_target_commit_sha=expected_target_commit_sha,
        required_workflow_names=required_workflow_names,
    )


def execute_fast_forward(
    *,
    repository_root: Path,
    repository: str,
    source_branch: str,
    target_branch: str,
    source_commit: str,
    expected_target_commit_sha: str,
    required_workflow_names: tuple[str, ...],
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
    request = local_main_fast_forward_request(
        repository_root=repository_root,
        repository=repository,
        source_branch=source_branch,
        target_branch=target_branch,
        source_commit=source_commit,
        expected_target_commit_sha=expected_target_commit_sha,
        required_workflow_names=required_workflow_names,
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
        "contents": "write",
    }
    manifest = GitHubAppManifest.from_mapping(
        {
            "schema": "evidence-lane.github-app-manifest.v1",
            "app_slug": "evidence-lane",
            "manifest_version": "1",
            "repository_selection": "selected",
            "repository_permissions": permissions,
            "events": ["push", "workflow_run"],
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
        receipt = GitHubAppMainFastForwardRoute(
            broker=broker,
            transport=transport,
        ).execute(request, token_request=token_request, now=requested_at)
    finally:
        transport.close()
    if receipt_contains_secret(receipt):
        raise GitHubAppMainFastForwardError(
            "The main fast-forward receipt contains a secret field."
        )
    output = receipt_path.resolve()
    content = canonical_json_bytes(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() != content:
        raise GitHubAppMainFastForwardError(
            "Refusing to overwrite another GitHub App main fast-forward receipt."
        )
    if not output.exists():
        output.write_bytes(content)
    promotion = receipt["promotion"]
    return {
        "status": "PASS",
        "route": receipt["route"],
        "repository": receipt["repository"],
        "source_branch": promotion["source_branch"],
        "source_commit": promotion["source_commit"],
        "target_branch": promotion["target_branch"],
        "main_commit": promotion["main_commit"],
        "receipt": str(output),
        "receipt_sha256": receipt["receipt_sha256"],
        "file_sha256": sha256_bytes(content),
        "credentials_persisted": False,
        "blob_reupload_count": 0,
        "force_push": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--target-branch", default="main")
    parser.add_argument("--source-commit", default="HEAD")
    parser.add_argument("--expected-target-commit-sha", required=True)
    parser.add_argument("--required-workflow", action="append", required=True)
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
    result = execute_fast_forward(
        repository_root=args.repository_root,
        repository=args.repository,
        source_branch=args.source_branch,
        target_branch=args.target_branch,
        source_commit=args.source_commit,
        expected_target_commit_sha=args.expected_target_commit_sha,
        required_workflow_names=tuple(args.required_workflow),
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
