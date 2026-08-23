from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.github_app_distribution import (
    EVIDENCE_LANE_APP_BOT_EMAIL,
    EVIDENCE_LANE_APP_BOT_NAME,
    GITHUB_APP_WEBHOOK_ROUTE,
    GITHUB_REST_API_VERSION,
    ArtifactEntitlementStore,
    DeterministicMockGitHubProvider,
    ExactGitCommitPushRequest,
    GitCommitActor,
    GitHubAPIResponse,
    GitHubAppExactCommitPushRoute,
    GitHubAppManifest,
    GitHubAppProductionDeliveryRoute,
    GitHubRESTInstallationTokenProvider,
    GitHubWebhookRoute,
    GitTreeChange,
    HttpxGitHubJSONTransport,
    InMemoryPEMGitHubAppJWTProvider,
    InstallationBinding,
    InstallationTokenBroker,
    InstallationTokenRequest,
    ProductionDeliveryIdentity,
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


def _binding(*, installation_id: str = "installation-7") -> InstallationBinding:
    manifest = _manifest()
    return InstallationBinding.create(
        binding_id="binding-1",
        manifest=manifest,
        installation_id=installation_id,
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
        {"metadata": "write"},
        {"metadata": "read", "actions": "write"},
        {"metadata": "read", "administration": "write"},
    ],
)
def test_manifest_blocks_overbroad_permissions(
    permissions: dict[str, str],
) -> None:
    with pytest.raises(EvidenceLaneError) as exc:
        _manifest(repository_permissions=permissions)
    assert exc.value.code == "GITHUB_APP_PERMISSION_OVERBROAD"


def test_manifest_allows_exact_private_app_source_and_workflow_write() -> None:
    manifest = _manifest(
        repository_permissions={
            "metadata": "read",
            "actions": "read",
            "checks": "write",
            "contents": "write",
            "workflows": "write",
        }
    )
    assert dict(manifest.repository_permissions)["contents"] == "write"
    assert dict(manifest.repository_permissions)["workflows"] == "write"
    assert manifest.public is False


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


class _RecordingWebhookHandler:
    handler_id = "TEST_CHECK_RUN_HANDLER"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def handle(self, *, event: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((event, payload))
        return {"outcome": "RECORDED", "action": payload.get("action")}


def _webhook_headers(*, secret: bytes, body: bytes) -> dict[str, str]:
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-GitHub-Delivery": "delivery-route-1",
        "X-GitHub-Event": "check_run",
        "X-Hub-Signature-256": signature,
    }


def test_public_webhook_route_authenticates_dispatches_once_and_hashes_result() -> None:
    secret = b"test-webhook-secret-32-bytes!!"
    body = json.dumps({"action": "completed", "check_run": {"id": 42}}).encode()
    handler = _RecordingWebhookHandler()
    route = GitHubWebhookRoute(
        verifier=WebhookVerifier(
            webhook_secret=secret,
            allowed_events=["check_run"],
        ),
        handler=handler,
    )
    request = {
        "method": "POST",
        "path": GITHUB_APP_WEBHOOK_ROUTE,
        "headers": _webhook_headers(secret=secret, body=body),
        "body": body,
        "received_at": NOW,
        "now": NOW,
    }
    first = route.dispatch(**request)
    replay = route.dispatch(**request)
    assert first["status"] == "PASS"
    assert first["raw_payload_persisted"] is False
    assert first["handler_result_persisted"] is False
    assert first["handler_id"] == handler.handler_id
    assert receipt_contains_secret(first) is False
    assert replay["status"] == "IDEMPOTENT_REPLAY"
    assert replay["handler_reinvoked"] is False
    assert len(handler.calls) == 1


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"path": "/api/not-evidence-lane"}, "GITHUB_APP_WEBHOOK_ROUTE_INVALID"),
        (
            {"headers": {"Content-Type": "text/plain"}},
            "GITHUB_APP_WEBHOOK_CONTENT_TYPE_INVALID",
        ),
        ({"body": b""}, "GITHUB_APP_WEBHOOK_BODY_BOUND_EXCEEDED"),
    ],
)
def test_public_webhook_route_rejects_invalid_surface(
    overrides: dict[str, object], expected: str
) -> None:
    secret = b"test-webhook-secret-32-bytes!!"
    body = b'{"action":"completed"}'
    route = GitHubWebhookRoute(
        verifier=WebhookVerifier(
            webhook_secret=secret,
            allowed_events=["check_run"],
        ),
        handler=_RecordingWebhookHandler(),
    )
    request: dict[str, object] = {
        "method": "POST",
        "path": GITHUB_APP_WEBHOOK_ROUTE,
        "headers": _webhook_headers(secret=secret, body=body),
        "body": body,
        "received_at": NOW,
        "now": NOW,
    }
    request.update(overrides)
    with pytest.raises(EvidenceLaneError) as exc:
        route.dispatch(**request)  # type: ignore[arg-type]
    assert exc.value.code == expected


def test_token_broker_is_provider_neutral_short_lived_and_secret_safe() -> None:
    provider = DeterministicMockGitHubProvider(b"provider-test-seed-32-bytes!!!")
    broker = InstallationTokenBroker(
        manifest=_manifest(), binding=_binding(), provider=provider
    )
    request = _token_request()
    token, receipt = broker.issue(request, now=NOW)
    replay_token, replay_receipt = broker.issue(request, now=NOW)
    assert token.startswith("mock-installation-token-")
    assert replay_token == token
    assert replay_receipt["idempotent_reuse"] is True
    assert token not in json.dumps(receipt)
    assert receipt["token_value_persisted"] is False
    assert receipt_contains_secret(receipt) is False
    assert len(provider.calls) == 1


class _TestAppJWTProvider:
    provider_id = "TEST_APP_JWT_SIGNER"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def issue_app_jwt(self, *, requested_at: str) -> str:
        self.calls.append(requested_at)
        return "test-app-jwt-value-never-persisted"


class _TestGitHubTransport:
    transport_id = "TEST_GITHUB_HTTPS_TRANSPORT"

    def __init__(self, response: GitHubAPIResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request_json(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> GitHubAPIResponse:
        self.calls.append(
            {"method": method, "path": path, "headers": headers, "body": body}
        )
        return self.response


def _github_token_response(**overrides: object) -> GitHubAPIResponse:
    body: dict[str, object] = {
        "token": "test-installation-token-never-persisted",
        "expires_at": "2026-08-14T12:30:00Z",
        "permissions": {
            "metadata": "read",
            "actions": "read",
            "checks": "write",
        },
        "repository_selection": "selected",
        "repositories": [{"full_name": "owner/repo"}],
    }
    body.update(overrides)
    return GitHubAPIResponse(status_code=201, body=body, request_id="github-req-1")


def test_production_github_provider_uses_exact_endpoint_scope_and_redacted_proof() -> (
    None
):
    jwt_provider = _TestAppJWTProvider()
    transport = _TestGitHubTransport(_github_token_response())
    provider = GitHubRESTInstallationTokenProvider(
        jwt_provider=jwt_provider,
        transport=transport,
    )
    broker = InstallationTokenBroker(
        manifest=_manifest(),
        binding=_binding(installation_id="7"),
        provider=provider,
    )
    token, broker_receipt = broker.issue(_token_request(installation_id="7"), now=NOW)
    assert token == "test-installation-token-never-persisted"
    assert len(jwt_provider.calls) == 1
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/app/installations/7/access_tokens"
    assert call["body"] == {
        "repositories": ["repo"],
        "permissions": {
            "actions": "read",
            "checks": "write",
            "metadata": "read",
        },
    }
    headers = call["headers"]
    assert isinstance(headers, Mapping)
    assert headers["X-GitHub-Api-Version"] == GITHUB_REST_API_VERSION
    assert headers["Authorization"].startswith("Bearer ")
    provider_receipt = provider.last_integration_receipt
    assert provider_receipt is not None
    assert provider_receipt["app_jwt_persisted"] is False
    assert provider_receipt["installation_token_persisted"] is False
    assert receipt_contains_secret(provider_receipt) is False
    assert token not in json.dumps(provider_receipt)
    assert token not in json.dumps(broker_receipt)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            _github_token_response(permissions={"metadata": "read", "actions": "read"}),
            "GITHUB_APP_PROVIDER_PERMISSION_DRIFT",
        ),
        (
            _github_token_response(repositories=[{"full_name": "owner/other"}]),
            "GITHUB_APP_PROVIDER_REPOSITORY_DRIFT",
        ),
        (
            GitHubAPIResponse(status_code=403, body={}),
            "GITHUB_APP_PROVIDER_REQUEST_FAILED",
        ),
    ],
)
def test_production_github_provider_fails_closed_on_provider_drift(
    response: GitHubAPIResponse, expected: str
) -> None:
    provider = GitHubRESTInstallationTokenProvider(
        jwt_provider=_TestAppJWTProvider(),
        transport=_TestGitHubTransport(response),
    )
    with pytest.raises(EvidenceLaneError) as exc:
        provider.issue(_token_request(installation_id="7"))
    assert exc.value.code == expected


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
        _token_request(permissions={"metadata": "read", "actions": "write"})
    assert broad.value.code == "GITHUB_APP_PERMISSION_OVERBROAD"


def test_in_memory_pem_signer_uses_current_github_rs256_claim_contract() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    provider = InMemoryPEMGitHubAppJWTProvider(
        client_id="Iv1.evidence-lane-test",
        private_key_pem=pem,
    )
    token = provider.issue_app_jwt(requested_at=NOW)
    claims = jwt.decode(
        token,
        private_key.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "Iv1.evidence-lane-test"
    assert claims["exp"] - claims["iat"] == 600


def test_httpx_transport_is_https_pinned_and_returns_bounded_json() -> None:
    def handler(request):
        assert str(request.url) == "https://api.github.com/app"
        return httpx.Response(
            200,
            json={"slug": "evidence-lane"},
            headers={"x-github-request-id": "github-request-1"},
        )

    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    transport = HttpxGitHubJSONTransport(client=client)
    response = transport.request_json(
        method="GET",
        path="/app",
        headers={"Accept": "application/vnd.github+json"},
        body={},
    )
    assert response.status_code == 200
    assert response.body == {"slug": "evidence-lane"}
    assert response.request_id == "github-request-1"
    with pytest.raises(EvidenceLaneError) as origin:
        HttpxGitHubJSONTransport(base_url="http://api.github.com")
    assert origin.value.code == "GITHUB_APP_TRANSPORT_ORIGIN_INVALID"
    client.close()


class _SequenceGitHubTransport:
    transport_id = "TEST_SEQUENCE_GITHUB_TRANSPORT"

    def __init__(self, responses: list[GitHubAPIResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request_json(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> GitHubAPIResponse:
        self.calls.append(
            {"method": method, "path": path, "headers": headers, "body": body}
        )
        assert self.responses
        return self.responses.pop(0)


def _write_manifest() -> GitHubAppManifest:
    return _manifest(
        repository_permissions={
            "metadata": "read",
            "actions": "read",
            "checks": "write",
            "contents": "write",
            "workflows": "write",
        }
    )


def _write_binding() -> InstallationBinding:
    manifest = _write_manifest()
    return InstallationBinding.create(
        binding_id="binding-write-1",
        manifest=manifest,
        installation_id="7",
        project_id="project-a",
        task_id="task-a",
        accepted_pv="PV12",
        repositories=["owner/repo"],
        permissions=dict(manifest.repository_permissions),
        expires_at="2026-08-14T13:00:00Z",
    )


def _write_token_request(**overrides: object) -> InstallationTokenRequest:
    raw: dict[str, object] = {
        "request_id": "write-token-request-1",
        "idempotency_key": "write-token-idem-1",
        "project_id": "project-a",
        "task_id": "task-a",
        "installation_id": "7",
        "repository": "owner/repo",
        "permissions": dict(_write_manifest().repository_permissions),
        "requested_at": NOW,
        "expires_at": "2026-08-14T12:30:00Z",
    }
    raw.update(overrides)
    return InstallationTokenRequest.create(**raw)  # type: ignore[arg-type]


def _exact_push_request(
    change: GitTreeChange,
    *,
    additional_parent_commit_shas: tuple[str, ...] = (),
) -> ExactGitCommitPushRequest:
    actor = GitCommitActor.create(
        name=EVIDENCE_LANE_APP_BOT_NAME,
        email=EVIDENCE_LANE_APP_BOT_EMAIL,
        date=NOW,
    )
    return ExactGitCommitPushRequest.create(
        request_id="push-request-1",
        idempotency_key="push-idem-1",
        project_id="project-a",
        task_id="task-a",
        repository="owner/repo",
        branch="agent/evi-v300-systemwide-release-hil-v3.0.0",
        expected_parent_commit_sha="1" * 40,
        additional_parent_commit_shas=additional_parent_commit_shas,
        expected_parent_tree_sha="2" * 40,
        expected_tree_sha="3" * 40,
        expected_commit_sha="4" * 40,
        commit_message="R249 exact governed checkpoint",
        author=actor,
        committer=actor,
        changes=[change],
    )


def test_private_app_route_creates_exact_git_objects_and_fast_forwards() -> None:
    change = GitTreeChange.create(
        path=".github/workflows/evidence-lane-preview-build.yml",
        mode="100644",
        content=b"name: Evidence Lane\n",
    )
    transport = _SequenceGitHubTransport(
        [
            GitHubAPIResponse(200, {"object": {"sha": "1" * 40}}, "req-1"),
            GitHubAPIResponse(200, {"tree": {"sha": "2" * 40}}, "req-2"),
            GitHubAPIResponse(201, {"sha": change.blob_sha}, "req-3"),
            GitHubAPIResponse(201, {"sha": "3" * 40}, "req-4"),
            GitHubAPIResponse(201, {"sha": "4" * 40}, "req-5"),
            GitHubAPIResponse(200, {"object": {"sha": "4" * 40}}, "req-6"),
            GitHubAPIResponse(200, {"object": {"sha": "4" * 40}}, "req-7"),
        ]
    )
    broker = InstallationTokenBroker(
        manifest=_write_manifest(),
        binding=_write_binding(),
        provider=DeterministicMockGitHubProvider(b"private-app-push-provider-seed"),
    )
    route = GitHubAppExactCommitPushRoute(broker=broker, transport=transport)
    request = _exact_push_request(change)
    receipt = route.execute(
        request,
        token_request=_write_token_request(),
        now=NOW,
    )
    replay = route.execute(
        request,
        token_request=_write_token_request(),
        now=NOW,
    )
    assert receipt["status"] == "PASS"
    assert receipt["commit_sha"] == "4" * 40
    assert receipt["commit_created"] is True
    assert receipt["ref_pushed"] is True
    assert receipt["force_push"] is False
    assert receipt["workflow_write_authorized"] is True
    assert receipt_contains_secret(receipt) is False
    assert replay["idempotent_reuse"] is True
    assert len(transport.calls) == 7
    assert transport.calls[-3]["body"]["author"]["name"] == EVIDENCE_LANE_APP_BOT_NAME
    assert transport.calls[-3]["body"]["committer"]["email"] == EVIDENCE_LANE_APP_BOT_EMAIL
    assert transport.calls[-3]["body"]["parents"] == ["1" * 40]
    assert transport.calls[-2]["method"] == "PATCH"
    assert transport.calls[-2]["body"] == {"sha": "4" * 40, "force": False}


def test_private_app_route_preserves_ordered_merge_parents() -> None:
    change = GitTreeChange.create(
        path="README.md",
        mode="100644",
        content=b"Evidence Lane\n",
    )
    request = _exact_push_request(
        change,
        additional_parent_commit_shas=("5" * 40,),
    )
    transport = _SequenceGitHubTransport(
        [
            GitHubAPIResponse(200, {"object": {"sha": "1" * 40}}, "req-1"),
            GitHubAPIResponse(200, {"tree": {"sha": "2" * 40}}, "req-2"),
            GitHubAPIResponse(201, {"sha": change.blob_sha}, "req-3"),
            GitHubAPIResponse(201, {"sha": "3" * 40}, "req-4"),
            GitHubAPIResponse(201, {"sha": "4" * 40}, "req-5"),
            GitHubAPIResponse(200, {"object": {"sha": "4" * 40}}, "req-6"),
            GitHubAPIResponse(200, {"object": {"sha": "4" * 40}}, "req-7"),
        ]
    )
    route = GitHubAppExactCommitPushRoute(
        broker=InstallationTokenBroker(
            manifest=_write_manifest(),
            binding=_write_binding(),
            provider=DeterministicMockGitHubProvider(
                b"private-app-push-provider-seed"
            ),
        ),
        transport=transport,
    )
    receipt = route.execute(request, token_request=_write_token_request(), now=NOW)

    assert receipt["status"] == "PASS"
    assert transport.calls[-3]["body"]["parents"] == ["1" * 40, "5" * 40]


def test_private_app_route_rejects_non_bot_commit_actor() -> None:
    change = GitTreeChange.create(path="README.md", mode="100644", content=b"x\n")
    human = GitCommitActor.create(
        name="Human",
        email="human@example.com",
        date=NOW,
    )
    with pytest.raises(EvidenceLaneError) as exc:
        ExactGitCommitPushRequest.create(
            request_id="push-human-1",
            idempotency_key="push-human-idem-1",
            project_id="project-a",
            task_id="task-a",
            repository="owner/repo",
            branch="agent/evi-v300-systemwide-release-hil-v3.0.0",
            expected_parent_commit_sha="1" * 40,
            expected_parent_tree_sha="2" * 40,
            expected_tree_sha="3" * 40,
            expected_commit_sha="4" * 40,
            commit_message="human commit is forbidden",
            author=human,
            committer=human,
            changes=[change],
        )
    assert exc.value.code == "GITHUB_APP_BOT_ACTOR_REQUIRED"


def test_private_app_route_rejects_workflow_push_without_workflows_write() -> None:
    change = GitTreeChange.create(
        path=".github/workflows/ci.yml",
        mode="100644",
        content=b"name: CI\n",
    )
    route = GitHubAppExactCommitPushRoute(
        broker=InstallationTokenBroker(
            manifest=_write_manifest(),
            binding=_write_binding(),
            provider=DeterministicMockGitHubProvider(b"private-app-push-provider-seed"),
        ),
        transport=_SequenceGitHubTransport([]),
    )
    with pytest.raises(EvidenceLaneError) as missing:
        route.execute(
            _exact_push_request(change),
            token_request=_write_token_request(
                permissions={"metadata": "read", "contents": "write"}
            ),
            now=NOW,
        )
    assert missing.value.code == "GITHUB_APP_PUSH_AUTHORITY_MISMATCH"


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


def _production_delivery(**overrides: object) -> ProductionDeliveryIdentity:
    commit = "a" * 40
    raw: dict[str, object] = {
        "delivery_id": "delivery-1",
        "installation_binding": _binding(),
        "repository": "owner/repo",
        "branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
        "commit_sha": commit,
        "tree_sha": "b" * 40,
        "actions_run_id": "run-42",
        "actions_status": "completed",
        "actions_conclusion": "success",
        "actions_head_sha": commit,
        "package_id": "package-42",
        "package_version": "3.0.0+codex.20260821090000.git.aaaaaaaaaaaa",
        "package_sha256": "C" * 64,
        "package_source_commit": commit,
        "mutable_local_slot": "evidence-lane-v300-testing-new",
        "branch_commit_slot": "evidence-lane-v300-branch-stable",
        "main_merge_fallback_slot": "evidence-lane-github",
        "installed_version": "3.0.0+codex.20260821090000.git.aaaaaaaaaaaa",
        "installed_package_sha256": "C" * 64,
        "installed_surface_sha256": "D" * 64,
        "main_merge_fallback_before_sha256": "E" * 64,
        "main_merge_fallback_after_sha256": "E" * 64,
    }
    raw.update(overrides)
    return ProductionDeliveryIdentity.create(**raw)  # type: ignore[arg-type]


def test_production_delivery_binds_github_package_and_three_slots_without_authority() -> (
    None
):
    route = GitHubAppProductionDeliveryRoute()
    identity = _production_delivery()
    receipt = route.seal(identity)

    assert receipt["status"] == "PASS"
    assert receipt["identity"]["source"]["commit_sha"] == "a" * 40
    assert receipt["identity"]["actions"]["head_sha"] == "a" * 40
    assert receipt["identity"]["package"]["source_commit"] == "a" * 40
    assert receipt["identity"]["package"]["sha256"] == "C" * 64
    assert receipt["identity"]["installation"]["package_sha256"] == "C" * 64
    assert receipt["identity"]["main_merge_fallback"]["unchanged"] is True
    assert receipt["commit_created"] is False
    assert receipt["ref_pushed"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["hil_inferred"] is False
    assert receipt_contains_secret(receipt) is False
    assert route.seal(identity) == receipt


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"actions_conclusion": "failure"}, "GITHUB_APP_DELIVERY_ACTIONS_NOT_GREEN"),
        ({"actions_head_sha": "f" * 40}, "GITHUB_APP_DELIVERY_COMMIT_MISMATCH"),
        (
            {"installed_package_sha256": "F" * 64},
            "GITHUB_APP_DELIVERY_PACKAGE_INSTALL_MISMATCH",
        ),
        (
            {"main_merge_fallback_after_sha256": "F" * 64},
            "GITHUB_APP_DELIVERY_MAIN_FALLBACK_MUTATED",
        ),
        (
            {"branch_commit_slot": "evidence-lane-v300-testing-new"},
            "GITHUB_APP_DELIVERY_SLOT_ALIAS_BLOCKED",
        ),
    ],
)
def test_production_delivery_fails_closed_on_identity_drift(
    overrides: dict[str, object], code: str
) -> None:
    with pytest.raises(EvidenceLaneError) as exc:
        _production_delivery(**overrides)
    assert exc.value.code == code


def test_production_delivery_replay_conflict_is_blocked() -> None:
    route = GitHubAppProductionDeliveryRoute()
    route.seal(_production_delivery())
    with pytest.raises(EvidenceLaneError) as exc:
        route.seal(
            _production_delivery(
                package_sha256="F" * 64,
                installed_package_sha256="F" * 64,
            )
        )
    assert exc.value.code == "GITHUB_APP_DELIVERY_REPLAY_CONFLICT"


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
