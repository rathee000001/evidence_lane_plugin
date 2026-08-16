from __future__ import annotations

import json

import pytest
from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.models import HostKind
from evidence_lane_plugin.persistence import route_persistence
from evidence_lane_plugin.runtime_host_classifier import (
    build_runtime_namespace,
    classify_runtime_host,
    validate_runtime_host_classifier,
    validate_runtime_namespace,
)


def _profile(model: str = "gpt-5.6-sol") -> dict[str, str]:
    return {
        "model": model,
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
    }


def _desktop_context(
    channel: str,
    *,
    surface: str = "CODEX",
    workspace_class: str = "CODEX_MANAGED_WORKTREE",
    model: str = "gpt-5.6-sol",
) -> dict[str, object]:
    return {
        "host_surface": {
            "container_channel": channel,
            "container_version": "26.813.1-beta",
            "active_surface": surface,
            "active_surface_evidence": "HOST_SESSION_TASK_CAPABILITY_RECEIPT",
            "workspace_class": workspace_class,
        },
        "execution_profile": _profile(model),
        "native_capabilities": {
            "exact_task_binding": True,
            "goal": True,
            "hooks": True,
            "host_plan": True,
            "local_filesystem": True,
        },
        "required_native_capabilities": [
            "exact_task_binding",
            "host_plan",
        ],
    }


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        (
            "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
            "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
        ),
        ("CHATGPT_DESKTOP_BETA", "CHATGPT_DESKTOP_BETA"),
    ],
)
def test_dual_surface_desktop_channels_bind_only_the_codex_surface(
    supplied: str,
    expected: str,
) -> None:
    receipt = classify_runtime_host(
        HostKind.CODEX_DESKTOP,
        ephemeral=False,
        server_has_durable_filesystem=True,
        runtime_context=_desktop_context(supplied),
        host_session_id="host-session-runtime-classifier",
    )

    assert validate_runtime_host_classifier(receipt) == receipt
    assert receipt["status"] == "PASS"
    assert receipt["container_channel"] == expected
    assert receipt["active_surface"] == "CODEX"
    assert receipt["workspace_class"] == "CODEX_MANAGED_WORKTREE"
    assert receipt["execution_profile"] == _profile()
    assert receipt["account_route"] == "HOST_ACCOUNT"
    assert receipt["chatgpt_chat_work_scope"] == "OUT_OF_SCOPE_DEFERRED"
    assert receipt["raw_process_title_cwd_used_as_authority"] is False


@pytest.mark.parametrize("surface", ["CHATGPT_CHAT", "CHATGPT_WORK"])
def test_chatgpt_surfaces_cannot_receive_codex_authority(surface: str) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        classify_runtime_host(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            server_has_durable_filesystem=True,
            runtime_context=_desktop_context(
                "CHATGPT_DESKTOP_BETA",
                surface=surface,
            ),
            host_session_id="host-session-cross-layer",
        )

    assert blocked.value.code == "ACTIVE_SURFACE_OUT_OF_SCOPE"


@pytest.mark.parametrize(
    "identity_only",
    [
        {"process_name": "ChatGPT.exe"},
        {"package_id": "com.openai.chatgpt.beta"},
        {"window_title": "Codex"},
        {"cwd": "F:/test codex"},
    ],
)
def test_process_package_title_or_cwd_alone_cannot_prove_surface(
    identity_only: dict[str, str],
) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        classify_runtime_host(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            server_has_durable_filesystem=True,
            runtime_context={
                "host_surface": {
                    "container_channel": "CHATGPT_DESKTOP_BETA",
                    **identity_only,
                }
            },
            host_session_id="host-session-ambiguous",
        )

    assert blocked.value.code == "ACTIVE_SURFACE_UNPROVEN"


def test_explicit_codex_surface_with_unavailable_evidence_fails_closed() -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        classify_runtime_host(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            server_has_durable_filesystem=True,
            runtime_context={
                "host_surface": {
                    "active_surface": "CODEX",
                    "active_surface_evidence": "HOST_CAPABILITY_UNAVAILABLE",
                }
            },
        )

    assert blocked.value.code == "ACTIVE_SURFACE_EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("host", "ephemeral", "durable", "context", "expected"),
    [
        (
            HostKind.CODEX_DESKTOP,
            False,
            True,
            {},
            "LOCAL_WORKSPACE",
        ),
        (
            HostKind.CODEX_DESKTOP,
            False,
            True,
            {"workspace_class": "CODEX_MANAGED_WORKTREE"},
            "CODEX_MANAGED_WORKTREE",
        ),
        (
            HostKind.CODEX_VM,
            False,
            True,
            {},
            "DURABLE_REMOTE_WORKSPACE",
        ),
        (
            HostKind.CODEX_VM,
            True,
            False,
            {},
            "EPHEMERAL_REMOTE_WORKSPACE",
        ),
    ],
)
def test_locality_and_workspace_classes_are_explicit(
    host: HostKind,
    ephemeral: bool,
    durable: bool,
    context: dict[str, str],
    expected: str,
) -> None:
    receipt = classify_runtime_host(
        host,
        ephemeral=ephemeral,
        server_has_durable_filesystem=durable,
        runtime_context=context,
        host_session_id="host-session-locality",
    )

    assert receipt["workspace_class"] == expected
    assert receipt["server_has_durable_filesystem"] is durable


def test_model_change_creates_a_distinct_classifier_receipt() -> None:
    first = classify_runtime_host(
        HostKind.CODEX_DESKTOP,
        ephemeral=False,
        server_has_durable_filesystem=True,
        runtime_context=_desktop_context("CHATGPT_DESKTOP_BETA"),
        host_session_id="host-session-model-change",
    )
    second = classify_runtime_host(
        HostKind.CODEX_DESKTOP,
        ephemeral=False,
        server_has_durable_filesystem=True,
        runtime_context=_desktop_context(
            "CHATGPT_DESKTOP_BETA",
            model="gpt-5.7-sol",
        ),
        host_session_id="host-session-model-change",
    )

    assert first["execution_profile"]["model"] == "gpt-5.6-sol"
    assert second["execution_profile"]["model"] == "gpt-5.7-sol"
    assert first["classifier_receipt_sha256"] != second[
        "classifier_receipt_sha256"
    ]


def test_strict_missing_host_capabilities_fail_closed() -> None:
    with pytest.raises(EvidenceLaneError) as durability_blocked:
        classify_runtime_host(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            server_has_durable_filesystem=None,
            runtime_context={"require_measured_capabilities": True},
            host_session_id="host-session-missing-durability",
        )
    assert durability_blocked.value.code == "DURABILITY_CAPABILITY_UNAVAILABLE"

    with pytest.raises(EvidenceLaneError) as native_blocked:
        classify_runtime_host(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            server_has_durable_filesystem=True,
            runtime_context={
                "required_native_capabilities": ["host_plan"],
                "native_capabilities": {"host_plan": False},
            },
            host_session_id="host-session-missing-plan",
        )
    assert native_blocked.value.code == (
        "REQUIRED_NATIVE_CAPABILITY_UNAVAILABLE"
    )


def test_storage_drive_and_additional_plugin_slots_remain_separate() -> None:
    route = route_persistence(
        HostKind.CODEX_DESKTOP,
        ephemeral=False,
        server_has_durable_filesystem=True,
        host_session_id="host-session-storage-separation",
    )
    classifier = route.runtime_classifier

    assert route.primary_runtime_authority == "LOCAL_DURABLE_SQLITE"
    assert route.google_drive_policy == "NOT_SELECTED_FOR_DURABLE_HOST"
    assert classifier["storage_connector_surface"] == (
        "SEPARATE_HOST_CAPABILITY_PERSISTENCE_ROUTE"
    )
    assert classifier["google_drive_surface"] == (
        "OPTIONAL_SEALED_ARTIFACT_FALLBACK_OR_MIRROR"
    )
    assert classifier["additional_plugin_slots_surface"] == (
        "EIGHT_GOVERNED_PLUGIN_OR_TOOLCHAIN_SLOTS_ONLY"
    )


def test_ephemeral_boot_never_silently_substitutes_a_connector(service) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        service.boot_session(
            project_id="book-faires",
            user_id="user-ephemeral",
            workspace_id="workspace-ephemeral",
            host="CODEX_VM",
            agent_id="codex-vm",
            sandbox_id="ephemeral-vm",
            ephemeral=True,
            runtime_context={"workspace_class": "EPHEMERAL_REMOTE_WORKSPACE"},
            host_session_id="host-session-ephemeral-no-connector",
            client_can_edit_source=True,
            server_has_durable_filesystem=False,
        )

    assert blocked.value.code == "DURABLE_RUNTIME_CONNECTOR_NOT_CONFIGURED"


def test_namespace_hash_prevents_project_session_workspace_and_version_collisions() -> None:
    classifier = classify_runtime_host(
        HostKind.CODEX_DESKTOP,
        ephemeral=False,
        server_has_durable_filesystem=True,
        runtime_context=_desktop_context("CHATGPT_DESKTOP_STABLE_OR_CURRENT"),
        host_session_id="host-session-namespace",
    )
    base = build_runtime_namespace(
        project_id="project-a",
        governed_session_id="session-a",
        workspace_id="workspace-a",
        host_session_id="host-session-namespace",
        classifier=classifier,
    )
    variants = [
        build_runtime_namespace(
            project_id="project-b",
            governed_session_id="session-a",
            workspace_id="workspace-a",
            host_session_id="host-session-namespace",
            classifier=classifier,
        ),
        build_runtime_namespace(
            project_id="project-a",
            governed_session_id="session-b",
            workspace_id="workspace-a",
            host_session_id="host-session-namespace",
            classifier=classifier,
        ),
        build_runtime_namespace(
            project_id="project-a",
            governed_session_id="session-a",
            workspace_id="workspace-b",
            host_session_id="host-session-namespace",
            classifier=classifier,
        ),
        build_runtime_namespace(
            project_id="project-a",
            governed_session_id="session-a",
            workspace_id="workspace-a",
            host_session_id="host-session-namespace",
            classifier=classifier,
            plugin_version=f"{ENGINE_VERSION}+next",
        ),
    ]

    assert validate_runtime_namespace(base) == base
    assert base["raw_host_session_id_stored"] is False
    assert base["raw_workspace_id_stored"] is False
    assert "host-session-namespace" not in json.dumps(base)
    assert "workspace-a" not in json.dumps(base)
    assert len({base["namespace_sha256"], *(v["namespace_sha256"] for v in variants)}) == 5
