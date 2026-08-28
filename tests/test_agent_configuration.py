from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.agent_configuration import (
    AgentConfigurationManager,
    resolve_agent_configuration,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_bytes
from evidence_lane_plugin.internal_sdk import (
    InternalEvidenceLaneSDK,
    RegisteredSDKAdapter,
    SDKBinding,
)
from evidence_lane_plugin.next_actions import resolve_direct_command_route
from evidence_lane_plugin.service import EvidenceLaneService


def _boot_local(application) -> dict:
    return application.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={
            "permission_mode": "test",
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "xhigh",
            "reasoning_speed": "standard",
        },
        host_session_id="host-session-test",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )


def _resolve(
    codex_home: Path,
    project_root: Path,
    cwd: Path,
    *,
    task_id: str = "01a02759-9842-74c1-860e-0c9a64f8258d",
):
    return resolve_agent_configuration(
        codex_home=codex_home,
        project_root=project_root,
        cwd=cwd,
        project_id="agent-config-test",
        governed_session_id="session-agent-config-test",
        host_task_id=task_id,
        host_task_deep_link=f"codex://threads/{task_id}",
        host_session_id=task_id,
        workspace_id=str(project_root),
        active_plan_task_id="EL-CODEX-AGENTS-MD-TEST",
        execution_profile={
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "xhigh",
            "reasoning_speed": "standard",
        },
    )


def test_global_and_project_discovery_merge_in_exact_precedence_order(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    nested = project / "src" / "feature"
    codex_home.mkdir()
    nested.mkdir(parents=True)
    (codex_home / "AGENTS.md").write_text("ignored global\n", encoding="utf-8")
    (codex_home / "AGENTS.override.md").write_text(
        "global override\n", encoding="utf-8"
    )
    (project / "AGENTS.md").write_text("project root\n", encoding="utf-8")
    (nested / "AGENTS.md").write_text("ignored nested\n", encoding="utf-8")
    (nested / "AGENTS.override.md").write_text("nested override\n", encoding="utf-8")

    resolved = _resolve(codex_home, project, nested)

    assert resolved.instructions.replace("\r\n", "\n") == (
        "global override\n\n\nproject root\n\n\nnested override\n"
    )
    chain = resolved.receipt["source_chain"]
    assert [row["locator"] for row in chain["sources"]] == [
        "CODEX_HOME/AGENTS.override.md",
        "PROJECT_ROOT/AGENTS.md",
        "PROJECT_ROOT/src/feature/AGENTS.override.md",
    ]
    assert resolved.receipt["project_doc_max_bytes"] == 32 * 1024
    assert resolved.receipt["raw_instruction_text_returned"] is False
    assert resolved.receipt["absolute_source_paths_returned"] is False
    assert resolved.receipt["authority_effects"]["permission_widening_allowed"] is False


def test_empty_sources_fall_through_to_configured_project_name(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    codex_home.mkdir()
    project.mkdir()
    (codex_home / "config.toml").write_text(
        'project_doc_fallback_filenames = ["TEAM_GUIDE.md"]\n'
        "project_doc_max_bytes = 32768\n",
        encoding="utf-8",
    )
    (project / "AGENTS.override.md").write_text(" \n", encoding="utf-8")
    (project / "AGENTS.md").write_text("\n", encoding="utf-8")
    (project / "TEAM_GUIDE.md").write_text("fallback rules", encoding="utf-8")

    resolved = _resolve(codex_home, project, project)

    assert resolved.instructions == "fallback rules"
    assert (
        resolved.receipt["source_chain"]["sources"][0]["precedence_kind"]
        == "CONFIGURED_FALLBACK"
    )


def test_missing_optional_codex_home_uses_default_global_configuration(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "not-installed-on-this-host"
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("project-only rules", encoding="utf-8")

    resolved = _resolve(codex_home, project, project)

    assert resolved.instructions == "project-only rules"
    assert [row["locator"] for row in resolved.receipt["source_chain"]["sources"]] == [
        "PROJECT_ROOT/AGENTS.md"
    ]


def test_resolution_accepts_model_only_profile_without_inventing_host_selectors(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    codex_home.mkdir()
    project.mkdir()

    resolved = resolve_agent_configuration(
        codex_home=codex_home,
        project_root=project,
        cwd=project,
        project_id="agent-config-test",
        governed_session_id="session-agent-config-test",
        host_task_id="01a02759-9842-74c1-860e-0c9a64f8258d",
        host_task_deep_link=("codex://threads/01a02759-9842-74c1-860e-0c9a64f8258d"),
        host_session_id="01a02759-9842-74c1-860e-0c9a64f8258d",
        workspace_id=str(project),
        active_plan_task_id="EL-CODEX-AGENTS-MD-TEST",
        execution_profile={"model": "gpt-5.6-sol"},
    )

    assert resolved.receipt["binding"]["execution_profile"] == {"model": "gpt-5.6-sol"}


@pytest.mark.parametrize("profile", [{}, {"model": "gpt-5.6-sol", "submodel": ""}])
def test_resolution_still_rejects_missing_model_or_invalid_supplied_selector(
    tmp_path: Path,
    profile: dict[str, str],
) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    codex_home.mkdir()
    project.mkdir()

    with pytest.raises(EvidenceLaneError) as blocked:
        resolve_agent_configuration(
            codex_home=codex_home,
            project_root=project,
            cwd=project,
            project_id="agent-config-test",
            governed_session_id="session-agent-config-test",
            host_task_id="01a02759-9842-74c1-860e-0c9a64f8258d",
            host_task_deep_link=(
                "codex://threads/01a02759-9842-74c1-860e-0c9a64f8258d"
            ),
            host_session_id="01a02759-9842-74c1-860e-0c9a64f8258d",
            workspace_id=str(project),
            active_plan_task_id="EL-CODEX-AGENTS-MD-TEST",
            execution_profile=profile,
        )

    assert blocked.value.code == "AGENT_CONFIGURATION_BINDING_INVALID"


def test_existing_codex_home_file_fails_closed(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    codex_home.write_text("not a directory", encoding="utf-8")
    project.mkdir()

    with pytest.raises(EvidenceLaneError) as blocked:
        _resolve(codex_home, project, project)

    assert blocked.value.code == "AGENT_CONFIGURATION_CODEX_HOME_INVALID"


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("invalid_utf8", "AGENT_CONFIGURATION_SOURCE_MALFORMED"),
        ("oversized", "AGENT_CONFIGURATION_SOURCE_OVERSIZED"),
        ("wrong_cwd", "AGENT_CONFIGURATION_WORKTREE_MISMATCH"),
        ("wrong_task", "AGENT_CONFIGURATION_TASK_BINDING_MISMATCH"),
    ],
)
def test_invalid_content_and_cross_boundary_inputs_fail_closed(
    tmp_path: Path,
    mode: str,
    expected_code: str,
) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    codex_home.mkdir()
    project.mkdir()
    cwd = project
    task_id = "01a02759-9842-74c1-860e-0c9a64f8258d"
    if mode == "invalid_utf8":
        (project / "AGENTS.md").write_bytes(b"\xff\xfe")
    elif mode == "oversized":
        (codex_home / "config.toml").write_text(
            "project_doc_max_bytes = 8\n", encoding="utf-8"
        )
        (project / "AGENTS.md").write_text("123456789", encoding="utf-8")
    elif mode == "wrong_cwd":
        cwd = tmp_path
    else:
        task_id = "different-task"

    with pytest.raises(EvidenceLaneError) as blocked:
        if mode == "wrong_task":
            resolve_agent_configuration(
                codex_home=codex_home,
                project_root=project,
                cwd=cwd,
                project_id="agent-config-test",
                governed_session_id="session-agent-config-test",
                host_task_id=task_id,
                host_task_deep_link=(
                    "codex://threads/01a02759-9842-74c1-860e-0c9a64f8258d"
                ),
                host_session_id=task_id,
                workspace_id=str(project),
                active_plan_task_id="EL-CODEX-AGENTS-MD-TEST",
                execution_profile={
                    "model": "gpt-5.6-sol",
                    "submodel": "sol",
                    "reasoning_effort": "xhigh",
                    "reasoning_speed": "standard",
                },
            )
        else:
            _resolve(codex_home, project, cwd, task_id=task_id)
    assert blocked.value.code == expected_code


def test_resolution_is_once_per_manager_but_restart_rebuilds_chain(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    codex_home.mkdir()
    project.mkdir()
    source = project / "AGENTS.md"
    source.write_text("first", encoding="utf-8")
    kwargs = {
        "codex_home": codex_home,
        "project_root": project,
        "cwd": project,
        "project_id": "agent-config-test",
        "governed_session_id": "session-agent-config-test",
        "host_task_id": "01a02759-9842-74c1-860e-0c9a64f8258d",
        "host_task_deep_link": ("codex://threads/01a02759-9842-74c1-860e-0c9a64f8258d"),
        "host_session_id": "01a02759-9842-74c1-860e-0c9a64f8258d",
        "workspace_id": str(project),
        "active_plan_task_id": "EL-CODEX-AGENTS-MD-TEST",
        "execution_profile": {
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "xhigh",
            "reasoning_speed": "standard",
        },
    }
    manager = AgentConfigurationManager()
    first = manager.resolve(**kwargs)
    source.write_text("second", encoding="utf-8")
    cached = manager.resolve(**kwargs)
    restarted = AgentConfigurationManager().resolve(**kwargs)

    assert cached.receipt["source_chain_sha256"] == first.receipt["source_chain_sha256"]
    assert (
        restarted.receipt["source_chain_sha256"] != first.receipt["source_chain_sha256"]
    )


def test_project_and_task_bindings_do_not_leak_across_resolutions(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    codex_home.mkdir()
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "AGENTS.md").write_text("first project", encoding="utf-8")
    (second_root / "AGENTS.md").write_text("second project", encoding="utf-8")

    first = _resolve(codex_home, first_root, first_root)
    second = _resolve(codex_home, second_root, second_root)

    assert first.instructions == "first project"
    assert second.instructions == "second project"
    assert first.receipt["binding_sha256"] != second.receipt["binding_sha256"]
    assert first.receipt["source_chain_sha256"] != second.receipt["source_chain_sha256"]
    assert "first" not in str(second.receipt["source_chain"]["sources"])


def test_command_and_sdk_bind_paired_hashes_without_widening_authority() -> None:
    authority_sha = sha256_bytes(b"agent-authority")
    chain_sha = sha256_bytes(b"agent-chain")
    authority = {
        "agent_configuration_authority_sha256": authority_sha,
        "source_chain_sha256": chain_sha,
    }
    route = resolve_direct_command_route(
        "/evi-refresh changed source",
        agent_configuration=authority,
    )
    assert route["agent_configuration_authority_sha256"] == authority_sha
    assert route["agent_configuration_source_chain_sha256"] == chain_sha
    assert route["agent_configuration_changes_route_authority"] is False
    assert route["hooks_required"] is False

    binding = SDKBinding.from_dict(
        {
            "project_id": "agent-config-test",
            "session_id": "session-agent-config-test",
            "task_id": "EL-CODEX-AGENTS-MD-TEST",
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "accepted_manifest_sha256": sha256_bytes(b"manifest"),
            "lineage_head_sha256": sha256_bytes(b"lineage"),
            "env_authority_sha256": sha256_bytes(b"env"),
            "uop_authority_sha256": sha256_bytes(b"uop"),
            "derived_projection_sha256": sha256_bytes(b"projection"),
            "flash_receipt_sha256": sha256_bytes(b"flash"),
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "xhigh",
            "reasoning_speed": "standard",
            "host_kind": "CODEX_DESKTOP",
            "host_session_id": "01a02759-9842-74c1-860e-0c9a64f8258d",
            "agent_configuration_authority_sha256": authority_sha,
            "agent_configuration_source_chain_sha256": chain_sha,
            "write_scope": [],
        }
    )
    assert binding.as_dict()["agent_configuration_authority_sha256"] == authority_sha


def test_sdk_public_receipt_exposes_paired_agent_configuration_hashes(
    tmp_path: Path,
) -> None:
    authority_sha = sha256_bytes(b"agent-authority")
    chain_sha = sha256_bytes(b"agent-chain")
    binding = SDKBinding.from_dict(
        {
            "project_id": "agent-config-test",
            "session_id": "session-agent-config-test",
            "task_id": "EL-CODEX-AGENTS-MD-TEST",
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "accepted_manifest_sha256": sha256_bytes(b"manifest"),
            "lineage_head_sha256": sha256_bytes(b"lineage"),
            "env_authority_sha256": sha256_bytes(b"env"),
            "uop_authority_sha256": sha256_bytes(b"uop"),
            "derived_projection_sha256": sha256_bytes(b"projection"),
            "flash_receipt_sha256": sha256_bytes(b"flash"),
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "xhigh",
            "reasoning_speed": "standard",
            "host_kind": "CODEX_DESKTOP",
            "host_session_id": "01a02759-9842-74c1-860e-0c9a64f8258d",
            "agent_configuration_authority_sha256": authority_sha,
            "agent_configuration_source_chain_sha256": chain_sha,
            "write_scope": [],
        }
    )
    root = tmp_path / binding.project_id
    root.mkdir()
    adapter = RegisteredSDKAdapter(
        adapter_id="agent-config-test-adapter.v1",
        snapshot_provider=lambda supplied: supplied.as_dict(),
        handlers={
            ("project_truth", "status"): lambda bound, payload, context: {
                "status": "PASS"
            }
        },
    )
    sdk = InternalEvidenceLaneSDK(root, adapter)

    response = sdk.invoke(
        module_id="project_truth",
        operation="status",
        binding=binding,
        payload={},
        request_id="agent-config-sdk-public-001",
    )

    assert response["agent_configuration_authority_sha256"] == authority_sha
    assert response["agent_configuration_source_chain_sha256"] == chain_sha
    assert response["authority_merge_allowed"] is False


def test_status_exposes_bounded_authority_with_hooks_off(
    service,
    source_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "AGENTS.md").write_text("global test rules", encoding="utf-8")
    (source_repository / "AGENTS.md").write_text("project test rules", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    booted = _boot_local(service)

    result = service.status_window("book-faires")

    authority = result["agent_configuration"]
    assert authority["status"] == "PASS"
    assert authority["source_count"] == 2
    assert authority["authority_effects"]["hooks_required_for_public_actions"] is False
    assert authority["raw_instruction_text_returned"] is False
    assert (
        booted["agent_configuration"]["agent_configuration_authority_sha256"]
        == authority["agent_configuration_authority_sha256"]
    )

    source_intake = service.source_intake(
        "book-faires",
        [str(source_repository)],
        session_id=booted["session"]["session_id"],
    )
    backlog = service.task_backlog_window("book-faires")
    for routed in (source_intake, backlog):
        assert (
            routed["agent_configuration"]["agent_configuration_authority_sha256"]
            == authority["agent_configuration_authority_sha256"]
        )

    restarted = EvidenceLaneService(data_root=service.store.root)
    restarted_authority = restarted.agent_configuration_authority(
        "book-faires",
        session_id=booted["session"]["session_id"],
    )
    assert (
        restarted_authority["source_chain_sha256"] == authority["source_chain_sha256"]
    )
    assert restarted_authority["binding_sha256"] == authority["binding_sha256"]
