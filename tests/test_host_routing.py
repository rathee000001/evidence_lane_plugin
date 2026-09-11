from datetime import UTC, datetime, timedelta

import pytest
from evidence_lane_plugin.connections import ClientRouter, ConnectRequest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import (
    HOST_MATRIX,
    ClientHello,
    HostDetector,
    select_host_route,
)
from evidence_lane_plugin.projects import ProjectDirectory
from pydantic import ValidationError


def detector(**overrides):
    return HostDetector(**{
        "system": lambda: "Windows", "machine": lambda: "AMD64", "cpu_count": lambda: 8,
        "which": lambda name: "measured executable" if name == "git" else None,
    } | overrides)


def test_host_matrix_preserves_all_five_prior_variants_without_attesting_them():
    assert {"codex_desktop_stable", "codex_desktop_beta", "codex_cli",
            "codex_vm_persistent", "codex_vm_ephemeral"} <= set(HOST_MATRIX)
    for profile in HOST_MATRIX:
        observation = detector().inspect(trigger="client_connect", client=ClientHello(
            configured_profile=profile, peer_name="Codex", peer_version="claimed",
            peer_capabilities=["sampling"], protocol="mcp_stdio",
        ))
        assert observation.host_profile_attestation == "unavailable"
        assert observation.native_task_attestation == "unavailable"
        assert observation.native_hooks == "unavailable"
        assert observation.model_identity == "unavailable"
        assert observation.available_commands == ["git"]
        assert observation.client_evidence_basis == "authenticated_client_report"


def test_unknown_clients_keep_local_access_without_fabricating_native_identity():
    observation = detector().inspect(trigger="client_connect")
    assert select_host_route(observation)["route"] == "local_loopback"
    assert not select_host_route(observation)["execution_authorized"]
    with pytest.raises(LaneError) as error:
        select_host_route(observation, exact_task_required=True)
    assert error.value.code == "NATIVE_TASK_ATTESTATION_UNAVAILABLE"


def test_ephemeral_and_persistent_vm_require_verified_durable_remote_route():
    for profile in ("codex_vm_persistent", "codex_vm_ephemeral"):
        observation = detector().inspect(trigger="client_connect", client=ClientHello(configured_profile=profile))
        assert select_host_route(observation, remote_ready=True)["route"] is None
        assert select_host_route(observation, durable_remote_verified=True)["reason"] == "remote_api_unavailable"
        assert select_host_route(observation, remote_ready=True, durable_remote_verified=True)["route"] == "remote_api"


def test_unavailable_local_transport_has_visible_reason():
    observation = detector(system=lambda: "UnimplementedOS").inspect(trigger="engine_start")
    assert observation.local_transport == "unavailable"
    assert select_host_route(observation)["reason"] == "local_transport_unavailable_remote_required"


def test_every_engine_start_and_new_client_uses_a_fresh_observation(tmp_path):
    calls = []
    current = datetime.now(UTC)

    def clock():
        calls.append(True)
        return current + timedelta(seconds=len(calls))

    probe = detector(clock=clock)
    with Engine(tmp_path, detector=probe) as engine:
        startup = engine.health().host_observation
        _, first = engine.clients.connect(ConnectRequest(claimed_task_id="claimed-a"))
        _, second = engine.clients.connect(ConnectRequest(claimed_task_id="claimed-b"))
        assert startup.trigger == "engine_start"
        assert first.host_observation.trigger == "client_connect"
        assert len({startup.observation_id, first.host_observation.observation_id,
                    second.host_observation.observation_id}) == 3
        assert startup.observed_at < first.host_observation.observed_at < second.host_observation.observed_at
        assert engine.clients.context(first, None, "read").native_task_id is None
    with Engine(tmp_path, detector=probe) as restarted:
        assert restarted.health().host_observation.observation_id != startup.observation_id
    assert len(calls) == 4


def test_public_hello_cannot_supply_attestation_or_unbounded_capabilities(tmp_path):
    with pytest.raises(ValidationError):
        ClientHello.model_validate({"native_task_attestation": "verified"})
    with pytest.raises(ValidationError):
        ClientHello(peer_capabilities=["goal_mutation"])
    router = ClientRouter(ProjectDirectory(tmp_path), detector=detector())
    _, session = router.connect(ConnectRequest(hello=ClientHello(configured_profile="codex_cli")))
    assert session.identity_evidence == "client_claim_only"
