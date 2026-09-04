"""Join exact-main-package, GitHub-App fast-forward, CI, and preview receipts.

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
BOUNDARY = "GOVERNED_GIT_MAIN_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[A-F0-9]{64}$")
_BOT_NAME = "evidence-lane[bot]"
_BOT_EMAIL = "319574480+evidence-lane[bot]@users.noreply.github.com"
_REQUIRED_MAIN_RULE_TYPES = frozenset(
    {
        "deletion",
        "non_fast_forward",
        "required_linear_history",
        "required_status_checks",
    }
)
_BRANCH_RULE_PAGE_SIZE = 100
_MAX_BRANCH_RULE_PAGES = 100


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


def _commit_actor_attestation(
    remote: dict[str, Any],
    *,
    repository: str,
    commit: str,
) -> dict[str, Any]:
    attestation = dict(remote.get("commit_actor_attestation") or {})
    author = dict(attestation.get("author") or {})
    committer = dict(attestation.get("committer") or {})
    expected_actor = {
        "login": _BOT_NAME,
        "name": _BOT_NAME,
        "email": _BOT_EMAIL,
    }
    expected_path = f"/repos/{repository}/commits/{commit}"
    if (
        attestation.get("provider") != "GITHUB_APP"
        or attestation.get("method") != "GET"
        or attestation.get("path") != expected_path
        or attestation.get("status") != 200
        or str(attestation.get("commit_sha") or "").lower() != commit
        or author != expected_actor
        or committer != expected_actor
        or attestation.get("author_and_committer_match_exact_bot") is not True
    ):
        raise ReleaseAuthorityError(
            "The GitHub App commit author/committer attestation is missing or mismatched."
        )
    return {
        "provider": "GITHUB_APP",
        "method": "GET",
        "path": expected_path,
        "status": 200,
        "commit_sha": commit,
        "author": expected_actor,
        "committer": expected_actor,
        "author_and_committer_match_exact_bot": True,
    }


def _branch_rule_snapshot(
    value: object,
    *,
    endpoint: str,
    expected_main_commit: str,
) -> dict[str, Any]:
    snapshot = dict(value) if isinstance(value, dict) else {}
    raw_pages = snapshot.get("pages")
    pages = list(raw_pages) if isinstance(raw_pages, list) else []
    if (
        str(snapshot.get("main_commit") or "").lower() != expected_main_commit
        or not pages
        or len(pages) > _MAX_BRANCH_RULE_PAGES
    ):
        raise ReleaseAuthorityError(
            "The GitHub App main branch-rules pagination attestation is incomplete."
        )
    rules: list[dict[str, Any]] = []
    page_paths: list[str] = []
    for page_number, raw_page in enumerate(pages, start=1):
        page = dict(raw_page) if isinstance(raw_page, dict) else {}
        page_rules = page.get("rules")
        exact_rules = list(page_rules) if isinstance(page_rules, list) else []
        expected_path = (
            f"{endpoint}?per_page={_BRANCH_RULE_PAGE_SIZE}&page={page_number}"
        )
        if (
            page.get("provider") != "GITHUB_APP"
            or page.get("method") != "GET"
            or page.get("path") != expected_path
            or page.get("status") != 200
            or not isinstance(page_rules, list)
            or len(exact_rules) > _BRANCH_RULE_PAGE_SIZE
            or (page_number < len(pages) and len(exact_rules) != _BRANCH_RULE_PAGE_SIZE)
        ):
            raise ReleaseAuthorityError(
                "The GitHub App main branch-rules pagination attestation is incomplete."
            )
        if any(not isinstance(rule, dict) for rule in exact_rules):
            raise ReleaseAuthorityError(
                "The GitHub App main branch-rules response is malformed."
            )
        rules.extend(dict(rule) for rule in exact_rules)
        page_paths.append(expected_path)
    if len(pages[-1].get("rules") or []) == _BRANCH_RULE_PAGE_SIZE:
        raise ReleaseAuthorityError(
            "The GitHub App main branch-rules pagination attestation is incomplete."
        )

    rule_types = {str(rule.get("type") or "") for rule in rules}
    contexts: set[str] = set()
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        parameters = dict(rule.get("parameters") or {})
        raw_checks = parameters.get("required_status_checks")
        checks = list(raw_checks) if isinstance(raw_checks, list) else []
        for raw_check in checks:
            check = dict(raw_check) if isinstance(raw_check, dict) else {}
            context = str(check.get("context") or "").strip()
            if context:
                contexts.add(context)
    protected_branch = _REQUIRED_MAIN_RULE_TYPES <= rule_types and bool(contexts)
    return {
        "main_commit": expected_main_commit,
        "page_count": len(pages),
        "page_paths": page_paths,
        "pagination_complete": True,
        "rule_count": len(rules),
        "rule_types": sorted(rule_types),
        "required_status_check_contexts": sorted(contexts),
        "protected_branch": protected_branch,
        "snapshot_sha256": hashlib.sha256(_json_bytes(snapshot)).hexdigest().upper(),
    }


def _branch_rules_attestation(
    remote: dict[str, Any],
    *,
    repository: str,
    commit: str,
    target_before_commit: str,
    required_check_names: list[str],
) -> dict[str, Any]:
    attestation = dict(remote.get("branch_rules_attestation") or {})
    endpoint = f"/repos/{repository}/rules/branches/main"
    if (
        attestation.get("provider") != "GITHUB_APP"
        or attestation.get("repository") != repository
        or attestation.get("branch") != "main"
        or attestation.get("endpoint") != endpoint
        or attestation.get("per_page") != _BRANCH_RULE_PAGE_SIZE
    ):
        raise ReleaseAuthorityError(
            "The GitHub App main branch-rules attestation is missing or mismatched."
        )
    before = _branch_rule_snapshot(
        attestation.get("before"),
        endpoint=endpoint,
        expected_main_commit=target_before_commit,
    )
    after = _branch_rule_snapshot(
        attestation.get("after"),
        endpoint=endpoint,
        expected_main_commit=commit,
    )
    required_contexts = set(required_check_names)
    if (
        before["protected_branch"] is not True
        or after["protected_branch"] is not True
        or not required_contexts
        or not required_contexts <= set(before["required_status_check_contexts"])
        or not required_contexts <= set(after["required_status_check_contexts"])
    ):
        raise ReleaseAuthorityError(
            "The attested main branch rules do not enforce the required governance."
        )
    return {
        "provider": "GITHUB_APP",
        "repository": repository,
        "branch": "main",
        "endpoint": endpoint,
        "per_page": _BRANCH_RULE_PAGE_SIZE,
        "before": before,
        "after": after,
        "protected_branch": bool(
            before["protected_branch"] and after["protected_branch"]
        ),
    }


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
    remote = _load_self_sealed(
        remote_git_receipt,
        remote_git_receipt_sha256,
        schema="evidence-lane.github-app-main-fast-forward.v1",
    )
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
    plugin_source_manifest_sha256 = str(
        export.get("plugin_source_manifest_sha256") or ""
    ).upper()
    plugin_source_member_count = export.get("plugin_source_member_count")
    remote_repository = dict(remote.get("repository_identity") or {})
    promotion = dict(remote.get("promotion") or {})
    authorization = dict(remote.get("authorization") or {})
    source_branch = str(promotion.get("source_branch") or "")
    checks = [dict(row) for row in ci.get("checks") or [] if isinstance(row, dict)]
    required_names = [str(name) for name in ci.get("required_check_names") or []]
    check_names = [str(row.get("name") or "") for row in checks]
    ci_repository = str(ci.get("repository") or "")
    target_before_commit = str(promotion.get("target_before_commit") or "").lower()
    actor_attestation = _commit_actor_attestation(
        remote,
        repository=ci_repository,
        commit=commit,
    )
    branch_rules = _branch_rules_attestation(
        remote,
        repository=ci_repository,
        commit=commit,
        target_before_commit=target_before_commit,
        required_check_names=required_names,
    )
    preview_source = dict(preview.get("source") or {})
    preview_deployment = dict(preview.get("deployment") or {})
    if (
        package.get("status") != "PASS"
        or package.get("boundary") != "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
        or package.get("archive", {}).get("filename") != archive.name
        or package.get("archive", {}).get("sha256") != _sha256(archive)
        or branch != "main"
        or export.get("source_ref") != "refs/remotes/origin/main"
        or export.get("stable_main_only") is not True
        or export.get("local_main_attested") is not True
        or export.get("origin_main_attested") is not True
        or _SHA1.fullmatch(commit) is None
        or _SHA1.fullmatch(tree) is None
        or export.get("projection_clean") is not True
        or export.get("working_checkout_bytes_used") is not False
        or export.get("untracked_bytes_used") is not False
        or _SHA256.fullmatch(plugin_source_manifest_sha256) is None
        or not isinstance(plugin_source_member_count, int)
        or plugin_source_member_count < 1
        or export.get("git_archive_member_count") != plugin_source_member_count
        or remote.get("route") != "github_app_main_fast_forward_v3"
        or remote.get("action") != "FAST_FORWARD_MAIN"
        or remote.get("status") != "PASS"
        or str(promotion.get("target_branch") or "") != "main"
        or not source_branch.startswith("agent/")
        or str(promotion.get("source_commit") or "").lower() != commit
        or str(promotion.get("main_commit") or "").lower() != commit
        or str(promotion.get("main_tree") or "").lower() != tree
        or int(promotion.get("ahead_by") or 0) < 1
        or promotion.get("behind_by") != 0
        or _SHA1.fullmatch(target_before_commit) is None
        or str(remote.get("repository") or "") != ci_repository
        or str(remote_repository.get("branch") or "") != "main"
        or str(remote_repository.get("commit_sha") or "").lower() != commit
        or str(remote_repository.get("tree_sha") or "").lower() != tree
        or authorization.get("policy") != "GOVERNED_FEATURE_TO_MAIN_FAST_FORWARD"
        or authorization.get("direct_main_implementation_authorized") is not False
        or authorization.get("fast_forward_authorized") is not True
        or authorization.get("merge_authorized") is not False
        or remote.get("github_commit_author_login") != _BOT_NAME
        or remote.get("source_tree_reused") is not True
        or int(remote.get("blob_reupload_count") or 0) != 0
        or remote.get("force_push") is not False
        or remote.get("direct_ref_patch_used") is not True
        or remote.get("credential_values_persisted") is not False
        or remote.get("private_key_persisted") is not False
        or remote.get("installation_token_persisted") is not False
        or remote.get("candidate_created_or_accepted") is not False
        or remote.get("pointer_moved") is not False
        or remote.get("hil_inferred") is not False
        or _SHA256.fullmatch(
            str(remote.get("token_broker_receipt_sha256") or "").upper()
        )
        is None
        or ci.get("status") != "PASS"
        or ci.get("branch") != source_branch
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
        or str(preview_source.get("branch") or "") != source_branch
        or str(preview_source.get("head_sha") or "").lower() != commit
        or preview_deployment.get("state") != "READY"
        or preview_deployment.get("target") != "PREVIEW"
        or not str(preview_deployment.get("deployment_id") or "").startswith("dpl_")
        or not str(preview_deployment.get("url") or "").endswith(".vercel.app")
        or not str(preview.get("project_id") or "").startswith("prj_")
        or not str(preview.get("team_id") or "").startswith("team_")
    ):
        raise ReleaseAuthorityError(
            "The exact main package, GitHub-App fast-forward, GitHub CI, and Vercel preview "
            "receipts do not join."
        )
    core = {
        "schema": SCHEMA,
        "status": "PASS",
        "boundary": BOUNDARY,
        "archive_sha256": _sha256(archive),
        "package_receipt_sha256": _sha256(package_receipt),
        "working_source_manifest_sha256": package["working_source_manifest_sha256"],
        "plugin_source_manifest_sha256": plugin_source_manifest_sha256,
        "plugin_source_member_count": plugin_source_member_count,
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
            "route": "github_app_main_fast_forward_v3",
            "promotion_status": "FAST_FORWARDED",
            "source_branch": source_branch,
            "target_branch": "main",
            "remote_branch_commit": commit,
            "protected_branch": branch_rules["protected_branch"],
            "commit_actor_attestation": actor_attestation,
            "branch_rules_attestation": branch_rules,
            "force_push": False,
            "source_tree_reused": True,
            "blob_reupload_count": 0,
            "native_receipt_sha256": _sha256(remote_git_receipt),
            "request_sha256": remote.get("request_sha256"),
            "token_broker_receipt_sha256": remote["token_broker_receipt_sha256"],
        },
        "github_ci": {
            "status": "PASS",
            "repository": ci.get("repository"),
            "branch": source_branch,
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
            "branch": source_branch,
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
