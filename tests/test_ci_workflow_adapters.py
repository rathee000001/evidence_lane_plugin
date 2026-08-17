from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.github_automation_governance import audit_workflow_action_pins

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION = ROOT / ".github" / "actions" / "evidence-lane-ci"


def test_all_workflow_action_references_are_immutable() -> None:
    receipt = audit_workflow_action_pins(WORKFLOWS)
    assert receipt["status"] == "PASS"
    assert receipt["file_count"] == 5
    assert receipt["reference_count"] == 21
    assert receipt["violation_count"] == 0
    assert receipt["kind_counts"] == {
        "LOCAL_SAME_COMMIT": 2,
        "REMOTE_FULL_COMMIT_SHA": 19,
    }


def test_workflow_branch_boundaries_and_preview_does_not_deploy() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "push:" in text:
            if path.name == "evidence-lane-github-pages.yml":
                assert "      - main" in text
                assert "      - agent/evi-v300-systemwide-release-hil-v3.0.0" in text
            else:
                assert '- "agent/**"' in text
                assert "branches:\n      - main" not in text
        assert "pull_request:" not in text
    preview = (WORKFLOWS / "evidence-lane-preview-build.yml").read_text(
        encoding="utf-8"
    )
    assert "vercel deploy" not in preview.lower()
    assert "--prod" not in preview.lower()
    assert "permissions:\n  contents: read" in preview
    assert "docker build" in preview
    assert "EVIDENCE_LANE_RELEASE_SHA=${EVIDENCE_LANE_RELEASE_SHA}" in preview
    assert "durable-image-identity.json" in preview
    assert "durable-image-health.json" in preview
    assert 'payload["release_sha"] == os.environ["EVIDENCE_LANE_RELEASE_SHA"]' in (
        preview
    )
    assert 'payload["mcp_route_identity"]["tool_count"] == 87' in preview

    pages = (WORKFLOWS / "evidence-lane-github-pages.yml").read_text(
        encoding="utf-8"
    )
    assert "  deploy:\n    if: github.ref == 'refs/heads/main'" in pages


def test_codeql_is_pinned_and_preserves_local_evidence_without_api_upload() -> None:
    text = (WORKFLOWS / "evidence-lane-codeql.yml").read_text(encoding="utf-8")
    assert text.count("@24c7eb380a2dc368f2d129e4c65e51d172983a1e") == 2
    assert "security-events: write" not in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
    assert "python,javascript-typescript" in text
    assert "upload: never" in text
    assert "upload-database: false" in text
    assert ".runtime/codeql/results" in text


def test_hosted_codeql_upload_is_manual_and_organization_owner_gated() -> None:
    text = (WORKFLOWS / "evidence-lane-codeql-hosted.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "github.repository_owner == 'Evidence-Lane'" in text
    assert "security-events: write" in text
    assert "upload: always" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text


def test_copilot_agent_profile_is_manual_bounded_and_not_an_actions_alias() -> None:
    profile = (ROOT / ".github" / "agents" / "evidence-lane.agent.md").read_text(
        encoding="utf-8"
    )
    instructions = (
        ROOT / ".github" / "copilot-instructions.md"
    ).read_text(encoding="utf-8")
    assert "target: github-copilot" in profile
    assert "disable-model-invocation: true" in profile
    assert "user-invocable: true" in profile
    assert "  - execute" in profile
    assert "  - terminal" not in profile
    assert "direct pushes to `main`" in profile
    assert "GitHub Sandbox" in profile
    assert "A passing test or Action is evidence only" in profile
    assert "Actions runs, Copilot coding-agent sessions" in instructions
    assert "Do not invoke GitHub Sandbox" in instructions
    assert "bounded local project work directory" in instructions
    assert "Never use one as proof of another" in instructions


def test_public_cost_boundary_excludes_github_sandbox_and_separates_vercel() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "does not configure or invoke the" in readme
    assert "usage-based GitHub Sandbox product" in readme
    assert "bounded local project work directory" in readme
    assert "paid overages and" in readme
    assert "The selected Vercel account plan does not change Evidence Lane authority" in (
        readme
    )
    assert "public documentation site only" in readme
    assert "Vercel is not used to install Codex" in readme
    assert "route the native lifecycle" in readme


def test_local_action_exposes_visible_code_mode_contract() -> None:
    metadata = (ACTION / "action.yml").read_text(encoding="utf-8")
    source = (ACTION / "src" / "main.mjs").read_text(encoding="utf-8")
    assert "using: node24" in metadata
    assert "main: src/main.mjs" in metadata
    assert "Fixed bounded profile" in metadata
    assert "plan -> sandbox build -> test -> hash -> package" in source
    assert "entry -> preflight -> sandbox -> patch -> test -> exit" in source
    assert "['CI/CD', 'PCM', 'MBA']" in source
    assert "function spawnGoverned(executable, args, options)" in source
    assert "['/d', '/c', executable, ...args]" in source
    assert "shell:" not in source
    assert "timeout: 12 * 60 * 1000" in source
    for profile in (
        "governed-quality",
        "governed-lifecycle",
        "governed-lanes",
        "governed-sources-mcp",
    ):
        assert f"'{profile}'" in source


def test_local_action_javascript_has_valid_node_syntax() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not available")
    result = subprocess.run(
        [node, "--check", str(ACTION / "src" / "main.mjs")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
