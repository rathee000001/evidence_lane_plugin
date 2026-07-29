from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.models import HostKind, normalize_host_kind
from evidence_lane_plugin.persistence import (
    InMemoryPersistence,
    PVSyncService,
    route_persistence,
)
from evidence_lane_plugin.redaction import contains_secret, redact
from evidence_lane_plugin.sealing import (
    deterministic_archive,
    seal_archive,
    unseal_archive,
)

from .conftest import build_and_approve_pv1


def test_secret_redaction_covers_common_tokens() -> None:
    payload = {
        "github": "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "openai": "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "header": "Authorization: Bearer-super-secret-value",
    }
    safe = redact(payload)
    assert safe != payload
    assert contains_secret(safe) is False


def test_host_persistence_matrix() -> None:
    assert route_persistence(HostKind.CODEX_DESKTOP, ephemeral=False).mode == "local"
    assert route_persistence(HostKind.CODEX_CLI, ephemeral=False).mode == "local"
    assert route_persistence(HostKind.CODEX_VM, ephemeral=True).mode == "google_drive"
    assert (
        route_persistence(
            HostKind.CHATGPT,
            ephemeral=False,
            server_has_durable_filesystem=True,
        ).mode
        == "local"
    )
    assert route_persistence(HostKind.CHATGPT, ephemeral=False).mode == "google_drive"
    assert (
        route_persistence(
            HostKind.CHATGPT,
            ephemeral=False,
            server_has_durable_filesystem=False,
        ).mode
        == "google_drive"
    )
    assert route_persistence(HostKind.PUBLIC_AI, ephemeral=False).mode == "google_drive"


def test_host_aliases_are_actionable() -> None:
    assert normalize_host_kind("codex") == HostKind.CODEX_DESKTOP
    assert normalize_host_kind("Codex Desktop") == HostKind.CODEX_DESKTOP
    assert normalize_host_kind("chatgpt") == HostKind.CHATGPT
    with pytest.raises(EvidenceLaneError) as error:
        normalize_host_kind("unknown-host")
    assert error.value.code == "HOST_KIND_INVALID"
    assert "CODEX_DESKTOP" in error.value.details["supported_values"]


def test_remote_host_fails_closed_without_drive(service) -> None:
    with pytest.raises(EvidenceLaneError) as error:
        service.boot_session(
            project_id="book-faires",
            user_id="user-test",
            workspace_id="workspace-test",
            host="CHATGPT_WORK",
            agent_id="chatgpt-agent",
            sandbox_id="remote-sandbox",
            ephemeral=True,
            runtime_context={},
        )
    assert error.value.code == "DURABLE_PERSISTENCE_NOT_CONFIGURED"


def test_bootstrap_installs_self_contained_noneditable_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (
        root / "plugins" / "evidence-lane-plugin" / "scripts" / "bootstrap.py"
    ).read_text(encoding="utf-8")
    requirements = (root / "requirements.in").read_text(encoding="utf-8")

    assert '"--force-reinstall"' in bootstrap
    assert '"--no-build-isolation"' in bootstrap
    assert '"--no-deps"' in bootstrap
    assert '"-e"' not in bootstrap
    assert 'project = plugin_root / "pyproject.toml"' in bootstrap
    assert "str(plugin_root)" in bootstrap
    assert "plugin_root.parents" not in bootstrap

    runner = (
        root / "plugins" / "evidence-lane-plugin" / "scripts" / "run_mcp.py"
    ).read_text(encoding="utf-8")
    assert "_bootstrap_runtime(plugin_root)" in runner

    mcp_config = json.loads(
        (root / "plugins" / "evidence-lane-plugin" / ".mcp.json").read_text(
            encoding="utf-8"
        )
    )
    server = mcp_config["mcpServers"]["evidence-lane"]
    assert server["startup_timeout_sec"] >= 600
    assert server["tool_timeout_sec"] >= 300
    assert "setuptools==83.0.0" in requirements
    assert "wheel==0.46.3" in requirements


def test_private_pv_is_encrypted_for_drive(service) -> None:
    _session_id, _ = build_and_approve_pv1(service)
    package = service.store.accepted_path("book-faires", "PV1")
    archive, metadata = deterministic_archive(package)
    key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    sealed, sealed_metadata, extension = seal_archive(
        archive,
        metadata,
        key_value=key,
        require_encryption=True,
    )
    assert extension == ".pv.enc.json"
    assert sealed_metadata["encryption"] == "AES-256-GCM"
    unsealed, recovered = unseal_archive(sealed, key_value=key)
    assert unsealed == archive
    assert recovered == metadata

    backend = InMemoryPersistence()
    sync = PVSyncService(
        store=service.store,
        backend=backend,
        drive_encryption_key=key,
    )
    receipt = sync.sync_pv("book-faires", "PV1", category="accepted")
    assert receipt["persistence"]["sha256"] == receipt["sealed"]["sealed_sha256"]
    assert list(backend.objects) == [("book-faires", "accepted", "PV1.pv.enc.json")]

    pointer = sync.sync_pointer("book-faires")
    pointer_key = (
        "book-faires",
        "receipts",
        "active-pointer-gen-00000001-PV1.json",
    )
    assert pointer["pointer"]["accepted_pv"] == "PV1"
    assert pointer["pointer"]["generation"] == 1
    assert backend.objects[pointer_key]["sha256"] == pointer["pointer_sha256"]


def test_remote_push_prepare_does_not_push_and_wrong_token_fails(service) -> None:
    build_and_approve_pv1(service)
    prepared = service.remote_git.prepare_push(
        "book-faires",
        requested_by="human-test",
        remote="origin",
        local_ref="main",
        remote_branch="evidence-lane-test",
    )
    assert prepared["action"]["status"] == "PREPARED_AWAITING_EXACT_CONFIRMATION"
    with pytest.raises(EvidenceLaneError) as error:
        service.remote_git.execute_push(
            "book-faires",
            action_id=prepared["action"]["action_id"],
            confirmation_token="WRONG",
            confirmed_by="human-test",
        )
    assert error.value.code == "REMOTE_ACTION_CONFIRMATION_INVALID"
