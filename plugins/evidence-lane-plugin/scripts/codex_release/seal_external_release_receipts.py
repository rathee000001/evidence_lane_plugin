"""Seal read-only GitHub CI and Vercel preview connector snapshots.

The input snapshots are captured from the host's read-only GitHub and Vercel
connectors. This utility validates exact branch/SHA binding and writes one
immutable self-sealed receipt. It performs no network, Git, deployment,
installation, lifecycle, candidate, pointer, or HIL action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

GITHUB_SNAPSHOT_SCHEMA = "evidence-lane.github-check-run-snapshot.v1"
GITHUB_RECEIPT_SCHEMA = "evidence-lane.github-ci-exact-head.v1"
VERCEL_SNAPSHOT_SCHEMA = "evidence-lane.vercel-deployment-snapshot.v1"
VERCEL_RECEIPT_SCHEMA = "evidence-lane.vercel-git-preview-exact-head.v1"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^(?:agent|feature|fix|test|tests|chore)/[A-Za-z0-9._/-]+$")


class ExternalReceiptError(RuntimeError):
    """Raised before output when a connector snapshot is not exact proof."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _load_snapshot(path: Path, schema: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != schema:
        raise ExternalReceiptError(f"Unexpected connector snapshot schema: {path.name}")
    return value


def _write_immutable(output: Path, core: dict[str, Any]) -> dict[str, Any]:
    core["receipt_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    content = _json_bytes(core)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() != content:
        raise ExternalReceiptError("Refusing to overwrite another authority receipt.")
    if not output.exists():
        output.write_bytes(content)
    return {**core, "receipt_path": str(output), "file_sha256": _sha256(output)}


def seal_github_ci_snapshot(*, snapshot: Path, output: Path) -> dict[str, Any]:
    snapshot = snapshot.resolve()
    value = _load_snapshot(snapshot, GITHUB_SNAPSHOT_SCHEMA)
    repository = str(value.get("repository") or "")
    branch = str(value.get("branch") or "")
    head_sha = str(value.get("head_sha") or "").lower()
    required = [str(name) for name in value.get("required_check_names") or []]
    checks = [dict(row) for row in value.get("checks") or [] if isinstance(row, dict)]
    check_names = [str(row.get("name") or "") for row in checks]
    if (
        value.get("provider") != "GITHUB_APP_READ_ONLY"
        or repository.count("/") != 1
        or _BRANCH.fullmatch(branch) is None
        or _SHA1.fullmatch(head_sha) is None
        or value.get("clean_checkout") is not True
        or not required
        or len(required) != len(set(required))
        or sorted(check_names) != sorted(required)
        or any(
            row.get("status") != "completed"
            or row.get("conclusion") != "success"
            or str(row.get("head_sha") or "").lower() != head_sha
            for row in checks
        )
    ):
        raise ExternalReceiptError(
            "GitHub snapshot does not prove every required check on the exact head."
        )
    core = {
        "schema": GITHUB_RECEIPT_SCHEMA,
        "status": "PASS",
        "repository": repository,
        "branch": branch,
        "head_sha": head_sha,
        "clean_checkout": True,
        "required_check_names": required,
        "checks": checks,
        "provider": "GITHUB_APP_READ_ONLY",
        "captured_at": value.get("captured_at"),
        "source_snapshot_sha256": _sha256(snapshot),
    }
    return _write_immutable(output, core)


def seal_vercel_preview_snapshot(*, snapshot: Path, output: Path) -> dict[str, Any]:
    snapshot = snapshot.resolve()
    value = _load_snapshot(snapshot, VERCEL_SNAPSHOT_SCHEMA)
    source = dict(value.get("source") or {})
    deployment = dict(value.get("deployment") or {})
    repository = str(source.get("repository") or "")
    branch = str(source.get("branch") or "")
    head_sha = str(source.get("head_sha") or "").lower()
    provider_target = deployment.get("target")
    target_is_preview = provider_target in (None, "", "preview", "PREVIEW")
    if (
        value.get("provider") != "VERCEL_APP_READ_ONLY"
        or not str(value.get("project_id") or "").startswith("prj_")
        or not str(value.get("team_id") or "").startswith("team_")
        or repository.count("/") != 1
        or _BRANCH.fullmatch(branch) is None
        or _SHA1.fullmatch(head_sha) is None
        or value.get("git_integration") is not True
        or value.get("manual_deploy") is not False
        or value.get("production_deployment") is not False
        or deployment.get("state") != "READY"
        or not target_is_preview
        or not str(deployment.get("deployment_id") or "").startswith("dpl_")
        or not str(deployment.get("url") or "").endswith(".vercel.app")
    ):
        raise ExternalReceiptError(
            "Vercel snapshot is not an exact READY Git-integrated branch preview."
        )
    core = {
        "schema": VERCEL_RECEIPT_SCHEMA,
        "status": "PASS",
        "project_id": value["project_id"],
        "team_id": value["team_id"],
        "source": {
            "repository": repository,
            "branch": branch,
            "head_sha": head_sha,
        },
        "deployment": {
            "deployment_id": deployment["deployment_id"],
            "url": deployment["url"],
            "state": "READY",
            "target": "PREVIEW",
            "provider_target": provider_target,
        },
        "git_integration": True,
        "manual_deploy": False,
        "production_deployment": False,
        "prior_successful_preview": value.get("prior_successful_preview"),
        "provider": "VERCEL_APP_READ_ONLY",
        "captured_at": value.get("captured_at"),
        "source_snapshot_sha256": _sha256(snapshot),
    }
    return _write_immutable(output, core)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)
    for kind in ("github-ci", "vercel-preview"):
        command = subparsers.add_parser(kind)
        command.add_argument("--snapshot", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = vars(_parser().parse_args())
    kind = arguments.pop("kind")
    if kind == "github-ci":
        result = seal_github_ci_snapshot(**arguments)
    else:
        result = seal_vercel_preview_snapshot(**arguments)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
