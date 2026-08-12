"""Join exact-package, native-push, GitHub-CI, and Vercel-preview receipts.

This is a read-only receipt joiner. It never invokes Git, GitHub, Evidence Lane,
Codex, installation, lifecycle, candidate, pointer, or HIL actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.codex-git-ci-vercel-release-authority.v2"
BOUNDARY = "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[A-F0-9]{64}$")


class ReleaseAuthorityError(RuntimeError):
    """Raised when the package, push, CI, and preview receipts do not join."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _load_exact(path: Path, expected_sha256: str) -> dict[str, Any]:
    expected = expected_sha256.strip().upper()
    if _SHA256.fullmatch(expected) is None or _sha256(path) != expected:
        raise ReleaseAuthorityError(f"Receipt file seal drifted: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_self_sealed(
    path: Path,
    expected_sha256: str,
    *,
    schema: str,
) -> dict[str, Any]:
    value = _load_exact(path, expected_sha256)
    receipt_sha256 = str(value.get("receipt_sha256") or "").upper()
    core = dict(value)
    core.pop("receipt_sha256", None)
    if (
        value.get("schema") != schema
        or _SHA256.fullmatch(receipt_sha256) is None
        or hashlib.sha256(_json_bytes(core)).hexdigest().upper() != receipt_sha256
    ):
        raise ReleaseAuthorityError(f"Receipt body seal drifted: {path.name}")
    return value


def seal_release_authority(
    *,
    archive: Path,
    package_receipt: Path,
    package_receipt_sha256: str,
    remote_git_receipt: Path,
    remote_git_receipt_sha256: str,
    github_ci_receipt: Path,
    github_ci_receipt_sha256: str,
    vercel_preview_receipt: Path,
    vercel_preview_receipt_sha256: str,
    output: Path,
) -> dict[str, Any]:
    archive = archive.resolve()
    package_receipt = package_receipt.resolve()
    remote_git_receipt = remote_git_receipt.resolve()
    github_ci_receipt = github_ci_receipt.resolve()
    vercel_preview_receipt = vercel_preview_receipt.resolve()
    package = _load_self_sealed(
        package_receipt,
        package_receipt_sha256,
        schema="evidence-lane.codex-exact-commit-package.v1.receipt",
    )
    remote = _load_exact(remote_git_receipt, remote_git_receipt_sha256)
    ci = _load_self_sealed(
        github_ci_receipt,
        github_ci_receipt_sha256,
        schema="evidence-lane.github-ci-exact-head.v1",
    )
    preview = _load_self_sealed(
        vercel_preview_receipt,
        vercel_preview_receipt_sha256,
        schema="evidence-lane.vercel-git-preview-exact-head.v1",
    )
    export = dict(package.get("exact_commit_export") or {})
    commit = str(export.get("commit") or "").lower()
    tree = str(export.get("tree") or "").lower()
    branch = str(export.get("branch") or "")
    remote_repository = dict(remote.get("repository_identity") or {})
    authorization = dict(remote.get("authorization") or {})
    output_security = dict(remote.get("output_security") or {})
    checks = [dict(row) for row in ci.get("checks") or [] if isinstance(row, dict)]
    required_names = [str(name) for name in ci.get("required_check_names") or []]
    check_names = [str(row.get("name") or "") for row in checks]
    preview_source = dict(preview.get("source") or {})
    preview_deployment = dict(preview.get("deployment") or {})
    if (
        package.get("status") != "PASS"
        or package.get("boundary") != "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
        or package.get("archive", {}).get("filename") != archive.name
        or package.get("archive", {}).get("sha256") != _sha256(archive)
        or _SHA1.fullmatch(commit) is None
        or _SHA1.fullmatch(tree) is None
        or export.get("projection_clean") is not True
        or export.get("working_checkout_bytes_used") is not False
        or export.get("untracked_bytes_used") is not False
        or remote.get("schema") != "evidence-lane.remote-git-action.v2"
        or remote.get("action") != "PUSH_BRANCH"
        or remote.get("status") != "EXECUTED"
        or not isinstance(remote.get("git_returncode"), int)
        or remote.get("git_returncode") != 0
        or str(remote.get("local_commit") or "").lower() != commit
        or str(remote.get("local_tree") or "").lower() != tree
        or str(remote.get("remote_branch") or "") != branch
        or str(remote_repository.get("branch") or "") != branch
        or str(remote_repository.get("commit_sha") or "").lower() != commit
        or str(remote_repository.get("tree_sha") or "").lower() != tree
        or authorization.get("policy")
        != "EXACT_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or authorization.get("main_branch_push_authorized") is not False
        or authorization.get("merge_authorized") is not False
        or output_security.get("infrastructure_status") != "PASS"
        or _SHA256.fullmatch(
            str(output_security.get("receipt_sha256") or "").upper()
        )
        is None
        or ci.get("status") != "PASS"
        or ci.get("branch") != branch
        or str(ci.get("head_sha") or "").lower() != commit
        or ci.get("clean_checkout") is not True
        or not required_names
        or len(required_names) != len(set(required_names))
        or sorted(check_names) != sorted(required_names)
        or any(
            row.get("status") != "completed"
            or row.get("conclusion") != "success"
            or str(row.get("head_sha") or "").lower() != commit
            for row in checks
        )
        or preview.get("status") != "PASS"
        or preview.get("git_integration") is not True
        or preview.get("manual_deploy") is not False
        or preview.get("production_deployment") is not False
        or str(preview_source.get("repository") or "")
        != str(ci.get("repository") or "")
        or str(preview_source.get("branch") or "") != branch
        or str(preview_source.get("head_sha") or "").lower() != commit
        or preview_deployment.get("state") != "READY"
        or preview_deployment.get("target") != "PREVIEW"
        or not str(preview_deployment.get("deployment_id") or "").startswith("dpl_")
        or not str(preview_deployment.get("url") or "").endswith(".vercel.app")
        or not str(preview.get("project_id") or "").startswith("prj_")
        or not str(preview.get("team_id") or "").startswith("team_")
    ):
        raise ReleaseAuthorityError(
            "The exact package, native push, GitHub CI, and Vercel preview "
            "receipts do not join."
        )
    core = {
        "schema": SCHEMA,
        "status": "PASS",
        "boundary": BOUNDARY,
        "archive_sha256": _sha256(archive),
        "package_receipt_sha256": _sha256(package_receipt),
        "working_source_manifest_sha256": package[
            "working_source_manifest_sha256"
        ],
        "source": {
            "branch": branch,
            "commit": commit,
            "tree": tree,
            "exact_commit_export": True,
            "exact_commit_projection_clean": True,
            "working_checkout_clean_required": False,
            "untracked_bytes_excluded": True,
        },
        "remote_git": {
            "route": "NATIVE_GOVERNED_REMOTE_GIT",
            "push_status": "EXECUTED",
            "remote_branch_commit": commit,
            "protected_branch": False,
            "native_receipt_sha256": _sha256(remote_git_receipt),
            "action_id": remote.get("action_id"),
            "output_security_receipt_sha256": output_security[
                "receipt_sha256"
            ],
        },
        "github_ci": {
            "status": "PASS",
            "repository": ci.get("repository"),
            "head_sha": commit,
            "clean_checkout": True,
            "required_checks_complete": True,
            "required_check_count": len(required_names),
            "successful_check_count": len(checks),
            "failed_check_count": 0,
            "required_check_names": required_names,
            "receipt_sha256": str(ci["receipt_sha256"]).upper(),
            "receipt_file_sha256": _sha256(github_ci_receipt),
        },
        "vercel_preview": {
            "status": "PASS",
            "project_id": preview["project_id"],
            "team_id": preview["team_id"],
            "deployment_id": preview_deployment["deployment_id"],
            "url": preview_deployment["url"],
            "state": "READY",
            "target": "PREVIEW",
            "repository": preview_source["repository"],
            "branch": branch,
            "head_sha": commit,
            "git_integration": True,
            "manual_deploy": False,
            "production_deployment": False,
            "prior_successful_preview": preview.get("prior_successful_preview"),
            "receipt_sha256": str(preview["receipt_sha256"]).upper(),
            "receipt_file_sha256": _sha256(vercel_preview_receipt),
        },
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "hil_inferred": False,
    }
    core["receipt_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    content = _json_bytes(core)
    if output.exists() and output.read_bytes() != content:
        raise ReleaseAuthorityError("Refusing to overwrite another authority receipt.")
    if not output.exists():
        output.write_bytes(content)
    return {**core, "receipt_path": str(output)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--package-receipt", type=Path, required=True)
    parser.add_argument("--package-receipt-sha256", required=True)
    parser.add_argument("--remote-git-receipt", type=Path, required=True)
    parser.add_argument("--remote-git-receipt-sha256", required=True)
    parser.add_argument("--github-ci-receipt", type=Path, required=True)
    parser.add_argument("--github-ci-receipt-sha256", required=True)
    parser.add_argument("--vercel-preview-receipt", type=Path, required=True)
    parser.add_argument("--vercel-preview-receipt-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    result = seal_release_authority(**vars(_parser().parse_args()))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
