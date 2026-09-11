"""HTTPS SDK/MCP transport with scoped credentials and no automatic mutation retry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import ssl
import time
from pathlib import Path

import httpx
from pydantic import Field, field_validator

from .endpoint_credentials import read_private_record, write_private_record
from .errors import LaneError
from .host_routing import ClientHello, HostDetector, select_host_route
from .projects import atomic_json
from .registry import Contract
from .remote_api import MAX_BYTES, TOKEN_PATTERN, ProbeVerification, https_origin
from .sdk import UUID_PATTERN, ActionRequest, ActionResponse
from .storage import reject_links


class RemoteClientConfig(Contract):
    origin: str
    server_id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    credential_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    ca_file: str | None = None
    probe_file: str | None = None
    hook_binding_directory: str | None = None

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value):
        return https_origin(value)

    @field_validator("ca_file", "probe_file", "hook_binding_directory")
    @classmethod
    def absolute_file(cls, value):
        if value is not None and not Path(value).is_absolute():
            raise ValueError("Configuration file paths must be absolute")
        return value

    @classmethod
    def load(cls, path: Path):
        path = path.absolute()
        reject_links(path, Path(path.anchor))
        if path.stat().st_size > 16_384:
            raise LaneError("REMOTE_CONFIG_INVALID", "The remote client configuration exceeds its budget.")
        return cls.model_validate_json(path.read_bytes())


class RemoteTransport:
    def __init__(self, config: RemoteClientConfig, *, hello: ClientHello | None = None,
                 environment=None, timeout: float = 20, monotonic=None):
        self.config = RemoteClientConfig.model_validate(config.model_dump())
        self.hello = hello or ClientHello()
        self.monotonic = monotonic or time.monotonic
        self.observation = HostDetector().inspect(trigger="client_connect", client=self.hello)
        self._verification: dict | None = None
        self._verified_at: float | None = None
        self._ticket: ProbeVerification | None = None
        self.connection = None
        environment = os.environ if environment is None else environment
        token = environment.get(config.credential_env, "")
        if not re.fullmatch(TOKEN_PATTERN, token):
            raise LaneError("REMOTE_CREDENTIAL_UNAVAILABLE", "Supply the configured scoped credential through its environment name.")
        if not 1 <= timeout <= 60:
            raise LaneError("REMOTE_TIMEOUT_INVALID", "Use a bounded remote request timeout.")
        if config.ca_file:
            path = Path(config.ca_file)
            reject_links(path, Path(path.anchor))
        context = ssl.create_default_context(cafile=config.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.http = httpx.Client(base_url=config.origin, verify=context, trust_env=False,
                                 follow_redirects=False, timeout=timeout,
                                 headers={"Authorization": f"Bearer {token}"})
        try:
            self.connected = self.post("discover", {"hello": self.hello.model_dump(mode="json")})
            self._bind(self.connected, instance=False)
            self.instance_id = self.connected["instance_id"]
            self.policy_digest = self.connected["policy_digest"]
            if config.probe_file:
                path = Path(config.probe_file)
                reject_links(path, Path(path.anchor))
                if path.exists():
                    if path.stat().st_size > 16_384:
                        raise LaneError("REMOTE_PROBE_INVALID", "The saved restart probe exceeds its budget.")
                    self.verify_storage(ProbeVerification.model_validate_json(path.read_bytes()))
        except BaseException:
            self.http.close()
            raise

    def post(self, route: str, payload: dict) -> dict:
        try:
            with self.http.stream("POST", "/remote/v4/" + route, json=payload) as response:
                # Redirects, proxy-environment changes and authentication downgrade
                # never forward the bearer credential to another destination.
                if response.status_code != 200:
                    details = {'http_status': response.status_code}
                    error_body = bytearray()
                    for chunk in response.iter_bytes():
                        error_body.extend(chunk)
                        if len(error_body) > 4096:
                            break
                    try:
                        error = json.loads(error_body) if len(error_body) <= 4096 else None
                        code = error.get('error') if isinstance(error, dict) else None
                        if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
                            details['remote_error'] = code
                    except ValueError:
                        pass
                    raise LaneError("REMOTE_REQUEST_FAILED", "The remote request failed; reconcile before retrying work.",
                                    details=details)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise LaneError("RESPONSE_TOO_LARGE", "The remote response exceeded its byte budget.")
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise TypeError()
                return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise LaneError("REMOTE_TRANSPORT_FAILED", "The authenticated remote request failed; reconcile before retrying work.") from None

    def _bind(self, result, *, instance=True):
        if (result.get("server_id") != self.config.server_id
                or result.get("project_id", self.config.project_id) != self.config.project_id
                or (instance and (result.get("instance_id") != self.instance_id
                                  or result.get("policy_digest") != self.policy_digest))):
            self._verification = None
            raise LaneError("REMOTE_BINDING_CHANGED", "The reply does not match the selected remote server, project and engine.")

    def seed_probe(self) -> ProbeVerification:
        nonce = secrets.token_urlsafe(48)
        result = self.post("probe", {"project_id": self.config.project_id, "nonce": nonce})
        self._bind(result)
        ticket = ProbeVerification(project_id=self.config.project_id, nonce=nonce,
                                   previous_engine_id=result["instance_id"],
                                   policy_digest=result["policy_digest"], object_digest=result["object_digest"])
        self._ticket, self._verification = ticket, None
        if self.config.probe_file:
            path = Path(self.config.probe_file)
            reject_links(path, Path(path.anchor))
            atomic_json(path, ticket.model_dump(mode="json"))
        return ticket

    def verify_storage(self, ticket: ProbeVerification) -> dict:
        self._verification = None
        if ticket.project_id != self.config.project_id:
            raise LaneError("REMOTE_PROBE_MISMATCH", "Select the restart probe for this project.")
        result = self.post("verify", ticket.model_dump(mode="json"))
        self._bind(result)
        if (result.get("object_digest") != ticket.object_digest
                or result.get("policy_digest") != ticket.policy_digest
                or result.get("restart_observed") != (self.instance_id != ticket.previous_engine_id)
                or result.get("storage_class") != self.connected.get("storage_class")
                or result.get("volume_id") != self.connected.get("volume_id")):
            raise LaneError("REMOTE_PROBE_MISMATCH", "The verification reply differs from the selected restart probe.")
        self._ticket, self._verification, self._verified_at = ticket, result, self.monotonic()
        return result

    def route(self) -> dict:
        fresh = self._verified_at is not None and 0 <= self.monotonic() - self._verified_at <= 300
        verified = bool(fresh and self._verification and self._verification.get("durable_verified") is True
                        and self._verification.get("restart_observed") is True
                        and self._verification.get("storage_class") == "persistent_operator_declared")
        route = select_host_route(self.observation, remote_ready=True, durable_remote_verified=verified,
                                  prefer_remote=True)
        return {**route, "server_id": self.config.server_id, "project_id": self.config.project_id,
                "durability": "restart_observed_operator_persistent" if verified else "unverified",
                "physical_volume_durability": "operator_declaration_only"}

    def _require_route(self):
        if self._ticket is not None and self._verified_at is not None and self.monotonic() - self._verified_at > 300:
            # Refresh only the read-only verification. Never retry/reconnect work.
            self.verify_storage(self._ticket)
        if self.route()["route"] != "remote_api":
            raise LaneError("REMOTE_DURABILITY_UNVERIFIED", "Verify the selected persistent remote store after an engine restart before execution.")

    def catalog(self) -> list[dict]:
        self._require_route()
        self._connect()
        discovered = self.post("discover", {"hello": self.hello.model_dump(mode="json")})
        self._bind(discovered)
        self.connected = discovered
        return self.connected["actions"]

    def send(self, request: ActionRequest) -> ActionResponse:
        self._require_route()
        if request.project_id != self.config.project_id:
            raise LaneError("REMOTE_SCOPE_DENIED", "Select the configured remote project.")
        if self.config.hook_binding_directory and request.action in {'session_boot', 'session_resume', 'capture_bind'}:
            names = {row['name'] for row in self.connected['actions']}
            if not {'capture_bind', 'session_status'} <= names:
                raise LaneError('REMOTE_HOOK_SCOPE_REQUIRED', 'Configured remote hooks require capture_bind and session_status in the owner grant.')
        self._connect()
        result = self.post("action", {**self._connection_envelope(),
                                       "request": request.model_dump(mode="json")})
        self._bind(result)
        response = ActionResponse.model_validate(result["response"])
        if (self.config.hook_binding_directory and response.status == 'ok'
                and request.action in {'session_boot', 'session_resume', 'capture_bind'}):
            self._save_hook_binding(request, response)
        return response

    def _save_hook_binding(self, request, response):
        try:
            # Historical replays cannot overwrite a successor's current binding.
            if request.action in {'session_boot', 'session_resume'}:
                current = self.send(ActionRequest(action='session_status', project_id=self.config.project_id))
                if (current.status != 'ok' or any(current.result.get(key) != response.result.get(key)
                        for key in ('session_id', 'generation', 'event_digest', 'owner_client_id'))
                        or current.result.get('owner_client_id') != self.connection['client_id']):
                    raise LaneError('REMOTE_CAPTURE_HEAD_CHANGED', 'The returned transition is not the current capture owner.')
            reported = request.arguments['reported_session_id']
            path = hook_binding_path(self.config, reported)
            reject_links(path, Path(path.anchor))
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            record = {'schema': 'evidence-lane.remote-hook-binding.v4', 'origin': self.config.origin,
                'server_id': self.config.server_id, 'project_id': self.config.project_id,
                'instance_id': self.instance_id, 'policy_digest': self.policy_digest,
                'client_id': self.connection['client_id'], 'expires_at': self.connection['expires_at'],
                'reported_session_id': reported, 'package_digest': self.connected['package_digest']}
            write_private_record(path, record, self.connection['connection_token'])
        except (OSError, ValueError, KeyError, TypeError, LaneError):
            raise LaneError('REMOTE_HOOK_BINDING_UNCONFIRMED',
                'The server action committed, but local hook binding was not confirmed; read session_status before continuing.',
                details={'committed_request_id': request.request_id, 'committed_action': request.action}) from None

    def _connect(self):
        if self.connection is None:
            result = self.post('connect', {'instance_id': self.instance_id, 'policy_digest': self.policy_digest,
                                          'hello': self.hello.model_dump(mode='json')})
            self._bind(result)
            if (not re.fullmatch(TOKEN_PATTERN, result.get('connection_token', ''))
                    or not re.fullmatch(UUID_PATTERN, result.get('client_id', ''))):
                raise LaneError('REMOTE_CONNECTION_INVALID', 'The server did not return a valid connection identity.')
            self.connection = result

    def _connection_envelope(self):
        if self.connection is None:
            raise LaneError('REMOTE_CONNECTION_REQUIRED', 'Open this authenticated connection before capture or work.')
        return {'instance_id': self.instance_id, 'policy_digest': self.policy_digest,
                'connection_token': self.connection['connection_token']}

    def capture(self, envelope):
        self._require_route()
        result = self.post('capture', {**self._connection_envelope(), 'capture': envelope.model_dump(mode='json')})
        self._bind(result)
        if result.get('result', {}).get('event_id') != envelope.event_id:
            raise LaneError('HOOK_DELIVERY_UNCONFIRMED', 'Remote visible capture did not return the expected event receipt.')
        return result

    def close(self):
        try:
            if self.connection is not None and not self.http.is_closed:
                self.post('disconnect', self._connection_envelope())
        except LaneError:
            # No retry or session takeover after an uncertain disconnect. Parent
            # revocation and bounded expiry continue to constrain the connection.
            pass
        finally:
            self.connection = None
            self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def main():
    parser = argparse.ArgumentParser(description="Verify an explicitly configured remote storage route")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seed-probe", action="store_true")
    args = parser.parse_args()
    config = RemoteClientConfig.load(args.config)
    if args.seed_probe and not config.probe_file:
        raise LaneError("REMOTE_PROBE_PATH_REQUIRED", "Select a local probe-file path before preparing a restart probe.")
    with RemoteTransport(config, hello=ClientHello(configured_profile="codex_vm_ephemeral")) as transport:
        if args.seed_probe:
            transport.seed_probe()
        print(json.dumps(transport.route()))


def hook_binding_path(config, reported_session_id):
    if not config.hook_binding_directory or not isinstance(reported_session_id, str) or not 1 <= len(reported_session_id) <= 128:
        raise LaneError('REMOTE_CAPTURE_BINDING_REQUIRED', 'Configure a private hook-binding directory and Boot this host session first.')
    return Path(config.hook_binding_directory) / (hashlib.sha256(reported_session_id.encode()).hexdigest() + '.json')


def submit_remote_hook(config, envelope, *, environment=None):
    """Use an existing bound connection; a hook never creates or resumes one."""
    reported = envelope.event['session_id']
    path = hook_binding_path(config, reported)
    reject_links(path, Path(path.anchor))
    if not path.exists():
        return {'captured': False, 'reason': 'project_session_not_bound'}
    record, token = read_private_record(path)
    from datetime import UTC, datetime
    if (record.get('schema') != 'evidence-lane.remote-hook-binding.v4'
            or record.get('origin') != config.origin or record.get('server_id') != config.server_id
            or record.get('project_id') != config.project_id or record.get('reported_session_id') != reported
            or datetime.fromisoformat(record['expires_at']) <= datetime.now(UTC)):
        raise LaneError('REMOTE_CAPTURE_BINDING_CHANGED', 'Refresh the configured remote capture binding through explicit session work.')
    # Discovery/verification are read-only. This helper never calls connect or
    # close/disconnect on the owner adapter's existing authenticated session.
    # Terminal hooks have a three-second host deadline. Ordinary events have
    # ten seconds and must allow their policy/context append to finish too.
    timeout = 1 if envelope.event['hook_event_name'] in {'Interrupt', 'SessionEnd'} else 3
    transport = RemoteTransport(config, environment=environment, timeout=timeout)
    try:
        if (record['instance_id'] != transport.instance_id or record['policy_digest'] != transport.policy_digest
                or record['package_digest'] != transport.connected['package_digest']):
            raise LaneError('REMOTE_CAPTURE_BINDING_CHANGED', 'The selected remote engine no longer matches this capture binding.')
        transport.connection = {**record, 'connection_token': token}
        try:
            result = transport.capture(envelope)
        except LaneError as error:
            if error.details.get('remote_error') in {'CAPTURE_BINDING_REQUIRED', 'REMOTE_CONNECTION_REQUIRED', 'CLIENT_SESSION_EXPIRED'}:
                return {'captured': False, 'reason': 'project_session_not_bound'}
            raise
        return {'captured': True, 'result': result['result'], 'engine_instance_id': result['instance_id']}
    finally:
        transport.connection = None
        transport.http.close()


if __name__ == "__main__":
    main()
