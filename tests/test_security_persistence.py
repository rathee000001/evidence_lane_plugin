from __future__ import annotations

import base64

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.models import HostKind
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
    assert route_persistence(HostKind.CHATGPT, ephemeral=False).mode == "google_drive"
    assert route_persistence(HostKind.PUBLIC_AI, ephemeral=False).mode == "google_drive"


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
