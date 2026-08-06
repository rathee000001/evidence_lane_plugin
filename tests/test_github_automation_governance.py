from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.github_automation_governance import (
    GITHUB_AW_GUARD_ORDER,
    GitHubAWPolicy,
    GitHubAWRequest,
    audit_workflow_action_pins,
    evaluate_github_aw_access,
    validate_action_reference,
)
from evidence_lane_plugin.mcp_server import create_mcp_server
from evidence_lane_plugin.service import EvidenceLaneService


def _policy(**changes: object) -> GitHubAWPolicy:
    raw: dict[str, object] = {
        "allowed_tools": ["get_file"],
        "allowed_repositories": ["owner/repo"],
        "allowed_roles": ["reader"],
        "allow_private_repositories": False,
        "blocked_users": ["blocked-user"],
        "minimum_content_integrity": "approved",
    }
    raw.update(changes)
    return GitHubAWPolicy.from_mapping(raw)


def _request(**changes: object) -> GitHubAWRequest:
    raw: dict[str, object] = {
        "tool_name": "get_file",
        "repository": "owner/repo",
        "actor": "allowed-user",
        "role": "reader",
        "repository_private": False,
        "content_integrity": "approved",
    }
    raw.update(changes)
    return GitHubAWRequest(**raw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "guard", "code", "trace_length"),
    [
        ({"tool_name": "push_file"}, "TOOL_ALLOWED", -32001, 1),
        ({"repository": "other/repo"}, "REPOSITORY_ALLOWED", -32002, 2),
        ({"role": "writer"}, "ROLE_ALLOWED", -32003, 3),
        (
            {"repository_private": True},
            "PRIVATE_REPOSITORY_ALLOWED",
            -32004,
            4,
        ),
        ({"actor": "blocked-user"}, "ACTOR_NOT_BLOCKED", -32005, 5),
        (
            {"content_integrity": "unapproved"},
            "CONTENT_INTEGRITY_SUFFICIENT",
            -32006,
            6,
        ),
    ],
)
def test_github_aw_access_fails_at_first_ordered_guard(
    changes: dict[str, object], guard: str, code: int, trace_length: int
) -> None:
    decision = evaluate_github_aw_access(_policy(), _request(**changes))
    assert decision["status"] == "BLOCKED"
    assert decision["decision"] == "DENY"
    assert decision["failed_guard"] == guard
    assert decision["code"] == code
    assert len(decision["guard_trace"]) == trace_length
    assert decision["guard_order"] == list(GITHUB_AW_GUARD_ORDER)


def test_github_aw_access_allows_exact_and_wildcard_repository_policies() -> None:
    exact = evaluate_github_aw_access(_policy(), _request())
    owner_wildcard = evaluate_github_aw_access(
        _policy(allowed_repositories=["owner/*"]),
        _request(repository="owner/another"),
    )
    repository_wildcard = evaluate_github_aw_access(
        _policy(allowed_repositories=["*/repo"]),
        _request(repository="another/repo"),
    )
    universal = evaluate_github_aw_access(
        _policy(allowed_repositories=["*/*"]),
        _request(repository="another/repository"),
    )
    assert {item["decision"] for item in (exact, owner_wildcard, repository_wildcard, universal)} == {"ALLOW"}
    assert len(exact["guard_trace"]) == 6


def test_github_aw_policy_rejects_empty_or_partial_authority() -> None:
    with pytest.raises(EvidenceLaneError, match="GITHUB_AW_POLICY_INVALID"):
        _policy(allowed_repositories=[])
    with pytest.raises(EvidenceLaneError, match="GITHUB_AW_POLICY_INVALID"):
        GitHubAWPolicy.from_mapping({"allowed_tools": ["get_file"]})


def test_action_reference_requires_same_commit_local_or_immutable_remote() -> None:
    full_sha = "3d3c42e5aac5ba805825da76410c181273ba90b1"
    docker_sha = "a" * 64
    assert validate_action_reference("./.github/actions/local")["status"] == "PASS"
    assert validate_action_reference(f"actions/checkout@{full_sha}")["kind"] == (
        "REMOTE_FULL_COMMIT_SHA"
    )
    assert validate_action_reference(f"docker://alpine@sha256:{docker_sha}")[
        "kind"
    ] == "DOCKER_SHA256_DIGEST"
    for reference in (
        "actions/checkout@v4",
        "actions/checkout@main",
        "actions/checkout@3d3c42e",
        "docker://alpine:latest",
        "${{ matrix.action }}",
    ):
        assert validate_action_reference(reference)["status"] == "BLOCKED"


def test_workflow_pin_audit_is_deterministic_and_reports_violations(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        """name: governed\nsteps:\n  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n  - uses: ./.github/actions/local\n""",
        encoding="utf-8",
    )
    first = audit_workflow_action_pins(tmp_path)
    second = audit_workflow_action_pins(tmp_path)
    assert first == second
    assert first["status"] == "PASS"
    assert first["file_count"] == 1
    assert first["reference_count"] == 2
    workflow.write_text(
        workflow.read_text(encoding="utf-8")
        + "  - uses: actions/upload-artifact@v4\n",
        encoding="utf-8",
    )
    blocked = audit_workflow_action_pins(tmp_path)
    assert blocked["status"] == "BLOCKED"
    assert blocked["violation_count"] == 1
    assert blocked["violations"][0]["code"] == (
        "WORKFLOW_ACTION_REF_NOT_IMMUTABLE"
    )


def test_fastmcp_exposes_only_exact_allowlisted_tools(tmp_path: Path) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store"),
        allowed_tool_names='["runtime_doctor","lane_catalog"]',
    )
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == {"runtime_doctor", "lane_catalog"}
    receipt = server._evidence_lane_tool_exposure_receipt  # type: ignore[attr-defined]
    assert receipt["mode"] == "EXACT_ALLOWLIST"
    assert receipt["exposed_tools"] == ["lane_catalog", "runtime_doctor"]
    assert "remote_git_execute_push" in receipt["removed_tools"]
    assert len(receipt["policy_sha256"]) == 64
    assert len(receipt["receipt_sha256"]) == 64


def test_fastmcp_rejects_unknown_allowlisted_tool(tmp_path: Path) -> None:
    with pytest.raises(EvidenceLaneError, match="MCP_TOOL_POLICY_UNKNOWN_TOOL"):
        create_mcp_server(
            service=EvidenceLaneService(data_root=tmp_path / "store"),
            allowed_tool_names='["not_a_registered_tool"]',
        )
