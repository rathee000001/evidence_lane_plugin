"""PyGithub read/delivery adapter with external secret references only."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SECRET_REF = re.compile(r"^[A-Za-z0-9_.:/-]{3,256}$")


class GitHubInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    secret_reference: str
    network_allowed: bool = False
    host_profile: str
    include_workflows: bool = True
    max_branches: int = Field(default=100, ge=1, le=500)


def inspect_github_repository(
    request: GitHubInspectionRequest,
    *,
    resolve_secret: Callable[[str], str],
) -> dict[str, Any]:
    if not request.network_allowed:
        raise ValueError("GITHUB_EXPLICIT_NETWORK_GRANT_REQUIRED")
    if request.host_profile.strip().upper() not in {
        "CODEX_DESKTOP",
        "CODEX_CLI",
        "CODEX_VM",
    }:
        raise ValueError("GITHUB_CODEX_HOST_PROFILE_REQUIRED")
    if not _REPOSITORY.fullmatch(request.repository):
        raise ValueError("GITHUB_REPOSITORY_IDENTITY_INVALID")
    if not _SECRET_REF.fullmatch(request.secret_reference):
        raise ValueError("GITHUB_SECRET_REFERENCE_INVALID")
    token = resolve_secret(request.secret_reference)
    if not token:
        raise ValueError("GITHUB_SECRET_REFERENCE_UNRESOLVED")
    from github import Auth, Github  # type: ignore[import-not-found]

    client = Github(auth=Auth.Token(token), timeout=30)
    try:
        repository = client.get_repo(request.repository)
        branches = [
            {
                "name": branch.name,
                "commit": branch.commit.sha,
                "protected": bool(branch.protected),
            }
            for index, branch in enumerate(repository.get_branches())
            if index < request.max_branches
        ]
        workflows = []
        if request.include_workflows:
            workflows = [
                {
                    "id": int(workflow.id),
                    "name": str(workflow.name),
                    "path": str(workflow.path),
                    "state": str(workflow.state),
                }
                for workflow in repository.get_workflows()
            ]
        core = {
            "schema": "evidence-lane.github-inspection.v1",
            "status": "PASS",
            "engine": "PyGithub",
            "repository": repository.full_name,
            "default_branch": repository.default_branch,
            "private": bool(repository.private),
            "archived": bool(repository.archived),
            "branches": branches,
            "workflows": workflows,
            "secret_reference_sha256": sha256_bytes(
                request.secret_reference.encode("utf-8")
            ),
            "secret_value_logged": False,
            "network_grant": True,
            "write_authorized": False,
        }
    finally:
        client.close()
        token = ""  # discard the resolved value before returning
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = ["GitHubInspectionRequest", "inspect_github_repository"]
