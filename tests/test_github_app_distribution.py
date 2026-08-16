from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.github_app_distribution import (
    ArtifactEntitlementStore,
    DeterministicMockGitHubProvider,
    GitHubAppManifest,
    InstallationBinding,
    InstallationTokenBroker,
    InstallationTokenRequest,
    WebhookVerifier,
    map_check_run_receipt,
    receipt_contains_secret,
)
from evidence_lane_plugin.github_app_distribution import (
    TesterRequest as ExternalTesterRequest,
)
from evidence_lane_plugin.hashing import sha256_bytes
from jsonschema import Draft202012Validator

NOW = "2026-08-14T12:00:00Z"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _manifest(**overrides: object) -> GitHubAppManifest:
    raw: dict[str, object] = {
        "schema": "evidence-lane.github-app-manifest.v1",
        "app_slug": "evidence-lane-test",
        "manifest_version": "1",
        "repository_selection": "selected",
        "repository_permissions": {
            "metadata": "read",
            "actions": "read",
            "checks": "write",
        },
        "events": ["check_run", "workflow_run"],
        "public": False,
    }
    raw.update(overrides)
    return GitHubAppManifest.from_mapping(raw)


def _binding() -> InstallationBinding:
    manifest = _manifest()
    return InstallationBinding.create(
        binding_id="binding-1",
        manifest=manifest,
        installation_id="installation-7",
        project_id="project-a",
        task_id="task-a",
        accepted_pv="PV12",
        repositories=["owner/repo"],
        permissions=dict(manifest.repository_permissions),
        expires_at="2026-08-14T13:00:00Z",
    )


def _token_request(**overrides: object) -> InstallationTokenRequest:
    raw: dict[str, object] = {
        "request_id": "request-1",
        "idempotency_key": "idem-1",
        "project_id": "project-a",
        "task_id": "task-a",
        "installation_id": "installation-7",
        "repository": "owner/repo",
        "permissions": {
            "metadata": "read",
            "actions": "read",
            "checks": "write",
        },
        "requested_at": NOW,
        "expires_at": "2026-08-14T12:30:00Z",
    }
    raw.update(overrides)
    return InstallationTokenRequest.create(**raw)  # type: ignore[arg-type]


def test_manifest_is_non_secret_selected_repository_and_least_privilege() -> None:
    manifest = _manifest()
    assert manifest.repository_selection == "selected"
    assert manifest.public is False
    assert dict(manifest.repository_permissions)["metadata"] == "read"
    assert "client_secret" not in json.dumps(manifest.as_dict())


def test_manifest_json_schema_matches_runtime_contract() -> None:
    schema = json.loads(
        (
            REPO_ROOT
            / "plugins"
            / "evidence-lane-plugin"
            / "src"
            / "evidence_lane_plugin"
            / "schemas"
            / "github-app-manifest.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_manifest().as_dict())


@pytest.mark.parametrize(
    "permissions",
    [
        {"metadata": "read", "contents": "write"},
        {"metadata": "read", "workflows": "write"},
        {"metadata": "write"},
    ],
)
def test_manifest_blocks_overbroad_permissions(
    permissions: dict[str, str],
) -> None:
    with pytest.raises(EvidenceLaneError) as exc:
        _manifest(repository_permissions=permissions)
    assert exc.value.code == "GITHUB_APP_PERMISSION_OVERBROAD"


def test_webhook_signature_is_checked_before_payload_parsing() -> None:
    verifier = WebhookVerifier(
        webhook_secret=b"test-webhook-secret-32-bytes!!",
        allowed_events=["check_run"],
    )
    with pytest.raises(EvidenceLaneError) as exc:
        verifier.verify(
            delivery_id="delivery-1",
            event="check_run",
            body=b"not-json",
            signature="sha256=" + "0" * 64,
            delivered_at=NOW,
            now=NOW,
        )
    assert exc.value.code == "GITHUB_APP_WEBHOOK_SIGNATURE_INVALID"
    assert exc.value.details["payload_parsed"] is False


def test_webhook_exact_duplicate_is_idempotent_but_changed_replay_fails() -> None:
    secret = b"test-webhook-secret-32-bytes!!"
    verifier = WebhookVerifier(
        webhook_secret=secret,
        allowed_events=["check_run"],
    )
    body = json.dumps({"action": "completed"}).encode()
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    _, first = verifier.verify(
        delivery_id="delivery-1",
        event="check_run",
        body=body,
        signature=signature,
        delivered_at=NOW,
        now=NOW,
    )
    _, replay = verifier.verify(
        delivery_id="delivery-1",
        event="check_run",
        body=body,
        signature=signature,
        delivered_at=NOW,
        now=NOW,
    )
    assert first["status"] == "PASS"
    assert replay["status"] == "IDEMPOTENT_REPLAY"
    changed = b'{"action":"rerequested"}'
    changed_signature = (
        "sha256=" + hmac.new(secret, changed, hashlib.sha256).hexdigest()
    )
    with pytest.raises(EvidenceLaneError) as exc:
        verifier.verify(
            delivery_id="delivery-1",
            event="check_run",
            body=changed,
            signature=changed_signature,
            delivered_at=NOW,
            now=NOW,
        )
    assert exc.value.code == "GITHUB_APP_WEBHOOK_REPLAY_CONFLICT"


def test_token_broker_is_provider_neutral_short_lived_and_secret_safe() -> None:
    provider = DeterministicMockGitHubProvider(b"provider-test-seed-32-bytes!!!")
    broker = InstallationTokenBroker(
        manifest=_manifest(), binding=_binding(), provider=provider
    )
    request = _token_request()
    token, receipt = broker.issue(request, now=NOW)
    replay_token, replay_receipt = broker.issue(request, now=NOW)
    assert token.startswith("ghs_mock_")
    assert replay_token == token
    assert replay_receipt["idempotent_reuse"] is True
    assert token not in json.dumps(receipt)
    assert receipt["token_value_persisted"] is False
    assert receipt_contains_secret(receipt) is False
    assert len(provider.calls) == 1


def test_token_broker_rejects_stale_cross_repository_and_overbroad_requests() -> None:
    provider = DeterministicMockGitHubProvider(b"provider-test-seed-32-bytes!!!")
    broker = InstallationTokenBroker(
        manifest=_manifest(), binding=_binding(), provider=provider
    )
    with pytest.raises(EvidenceLaneError) as stale:
        broker.issue(
            _token_request(
                requested_at="2026-08-14T11:00:00Z",
                expires_at="2026-08-14T12:10:00Z",
            ),
            now=NOW,
        )
    assert stale.value.code == "GITHUB_APP_TOKEN_REQUEST_STALE"
    with pytest.raises(EvidenceLaneError) as cross_repo:
        broker.issue(_token_request(repository="owner/other"), now=NOW)
    assert cross_repo.value.code == "GITHUB_APP_CROSS_REPOSITORY_ACCESS_BLOCKED"
    with pytest.raises(EvidenceLaneError) as broad:
        _token_request(permissions={"metadata": "read", "contents": "write"})
    assert broad.value.code == "GITHUB_APP_PERMISSION_OVERBROAD"


def test_successful_check_cannot_accept_or_fuse() -> None:
    receipt = map_check_run_receipt(
        project_id="project-a",
        task_id="task-a",
        repository="owner/repo",
        check_run_id="check-1",
        status="completed",
        conclusion="success",
    )
    assert receipt["candidate_accepted"] is False
    assert receipt["fuse_invoked"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["hil_inferred"] is False


def _entitlement_store() -> tuple[ArtifactEntitlementStore, bytes]:
    artifact = b"signed-package-bytes"
    store = ArtifactEntitlementStore(signing_key=b"artifact-signing-test-key-32bytes")
    request = ExternalTesterRequest.create(
        request_id="tester-request-1",
        tester_subject="host-subject-42",
        requested_scope="SIGNED_INSTALLER_ARTIFACT_ONLY",
        requested_at=NOW,
    )
    request_receipt = store.record_request(request)
    assert request_receipt["development_repository_access"] is False
    store.approve(
        entitlement_id="entitlement-1",
        request_id=request.request_id,
        artifact_id="artifact-1",
        artifact_sha256=sha256_bytes(artifact),
        terms_sha256="A" * 64,
        approved_by="owner-human",
        approved_at=NOW,
        expires_at="2026-08-15T12:00:00Z",
    )
    return store, artifact


def test_missing_entitlement_and_revocation_fail_closed() -> None:
    store = ArtifactEntitlementStore(signing_key=b"artifact-signing-test-key-32bytes")
    with pytest.raises(EvidenceLaneError) as missing:
        store.issue_authorization(
            entitlement_id="missing",
            authorization_id="auth-1",
            issued_at=NOW,
            expires_at="2026-08-14T12:10:00Z",
        )
    assert missing.value.code == "GITHUB_APP_ENTITLEMENT_MISSING"
    store, _ = _entitlement_store()
    store.revoke(
        entitlement_id="entitlement-1",
        revoked_by="owner-human",
        revoked_at="2026-08-14T12:01:00Z",
    )
    with pytest.raises(EvidenceLaneError) as revoked:
        store.issue_authorization(
            entitlement_id="entitlement-1",
            authorization_id="auth-1",
            issued_at="2026-08-14T12:02:00Z",
            expires_at="2026-08-14T12:10:00Z",
        )
    assert revoked.value.code == "GITHUB_APP_ENTITLEMENT_REVOKED"


def test_artifact_authorization_blocks_substitution_and_never_grants_repo_access() -> (
    None
):
    store, artifact = _entitlement_store()
    authorization, issue_receipt = store.issue_authorization(
        entitlement_id="entitlement-1",
        authorization_id="auth-1",
        issued_at=NOW,
        expires_at="2026-08-14T12:10:00Z",
    )
    assert authorization not in json.dumps(issue_receipt)
    with pytest.raises(EvidenceLaneError) as substituted:
        store.authorize_download(
            authorization=authorization,
            artifact_id="artifact-1",
            artifact_bytes=b"substituted",
            now="2026-08-14T12:05:00Z",
        )
    assert substituted.value.code == "GITHUB_APP_ARTIFACT_SUBSTITUTION_BLOCKED"
    receipt = store.authorize_download(
        authorization=authorization,
        artifact_id="artifact-1",
        artifact_bytes=artifact,
        now="2026-08-14T12:05:00Z",
    )
    assert receipt["development_repository_access"] is False
    assert receipt["source_write_authorized"] is False
    assert receipt_contains_secret(receipt) is False


def test_install_and_feedback_receipts_are_bounded_and_non_promoting() -> None:
    store, artifact = _entitlement_store()
    artifact_sha = sha256_bytes(artifact)
    install = store.record_installation(
        entitlement_id="entitlement-1",
        installation_receipt_id="install-1",
        artifact_id="artifact-1",
        artifact_sha256=artifact_sha,
        host_profile="WINDOWS_TEST_FIXTURE",
        result="VERIFIED",
        recorded_at="2026-08-14T12:06:00Z",
    )
    feedback = store.record_feedback(
        entitlement_id="entitlement-1",
        feedback_id="feedback-1",
        outcome="NEEDS_CORRECTION",
        visible_feedback="The disposable fixture reported one bounded issue.",
        recorded_at="2026-08-14T12:07:00Z",
    )
    assert install["installation_performed_by_contract"] is False
    assert install["pointer_moved"] is False
    assert feedback["raw_feedback_persisted"] is False
    assert feedback["authority_effect"] == "NONE"
    assert "bounded issue" not in json.dumps(feedback)


def test_tester_scope_cannot_be_development_repository() -> None:
    with pytest.raises(EvidenceLaneError) as exc:
        ExternalTesterRequest.create(
            request_id="tester-request-1",
            tester_subject="host-subject-42",
            requested_scope="DEVELOPMENT_REPOSITORY_READ",
            requested_at=NOW,
        )
    assert exc.value.code == "GITHUB_APP_TESTER_SCOPE_OVERBROAD"
