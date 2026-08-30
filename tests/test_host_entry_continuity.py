from __future__ import annotations

import copy
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_adapter import calculate_worktree_sha256
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.host_entry_continuity import (
    consume_host_entry_envelope,
    derive_host_entry_authority_heads,
    derive_host_entry_env_uop,
    inspect_host_entry_continuity,
    issue_host_entry_envelope,
    persist_host_entry_envelope,
    roll_host_entry_generation,
    seal_next_host_entry_from_exit,
)
from evidence_lane_plugin.lineage import ProjectChatLineage
from evidence_lane_plugin.persistence import (
    InMemoryPersistence,
    PVSyncService,
    route_persistence,
)

from .conftest import build_and_approve_pv1


def _hash(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _contract(
    *,
    generation: int = 12,
    ordinal: int = 1,
    profile: str = "STATELESS_HEADLESS",
    destination_task_id: str = "task-destination",
    candidate_overlay: dict | None = None,
) -> dict:
    remote = profile != "DURABLE_LOCAL"
    return {
        "evidence_session_id": "session-test",
        "plan_task_id": "EL-CODEX-LOCAL_EPHEMERAL_HEADLESS_ROUTING-PROPOSAL-22",
        "host_binding_id": "host-binding-01",
        "host_entry_ordinal": ordinal,
        "interaction_profile": profile,
        "accepted_pointer": {
            "accepted_pv": f"PV{generation}",
            "generation": generation,
            "manifest_sha256": _hash(f"manifest-{generation}"),
            "pointer_sha256": _hash(f"pointer-{generation}"),
        },
        "active_plan_row": 185,
        "source_task": {
            "task_id": "task-source",
            "task_deep_link_sha256": _hash("source-deep-link"),
        },
        "destination_task": {
            "task_id": destination_task_id,
            "task_deep_link_sha256": _hash(destination_task_id + "-deep-link"),
        },
        "env_uop": {
            "env_authority_sha256": _hash("env"),
            "uop_authority_sha256": _hash("uop"),
            "derived_projection_sha256": _hash("projection"),
            "flash_receipt_sha256": _hash("flash"),
        },
        "worktree": {
            "source_identity_sha256": _hash("source-identity"),
            "worktree_sha256": _hash("worktree"),
            "status_sha256": _hash("dirty-untracked-status"),
            "dirty_untracked_bytes_preserved": True,
        },
        "authority_heads": {
            "project_truth_pointer_sha256": _hash(f"pointer-{generation}"),
            "canon_input_head_sha256": _hash("canon-head"),
            "agent_learning_pointer_sha256": _hash("learning-head"),
            "chat_lineage_head_sha256": _hash("lineage-head"),
        },
        "previous_exit_sha256": _hash(f"exit-{ordinal}"),
        "storage": {
            "mode": "configured_durable_connector" if remote else "local",
            "connector_id": "tx-test" if remote else "LOCAL_DURABLE_SQLITE",
            "selection_receipt_sha256": _hash("storage-selection"),
            "transactional_exact_once_required": remote,
            "google_drive_primary_runtime": False,
        },
        "replay_nonce_sha256": _hash(f"nonce-{ordinal}"),
        "issued_at": "2026-08-13T12:00:00+00:00",
        "expires_at": "2026-08-14T12:00:00+00:00",
        "candidate_overlay": candidate_overlay,
    }


def _consumer(contract: dict) -> dict:
    return {
        "task_id": contract["destination_task"]["task_id"],
        "task_deep_link_sha256": contract["destination_task"][
            "task_deep_link_sha256"
        ],
        "host_binding_id": contract["host_binding_id"],
        "host_session_id_sha256": _hash("host-session"),
        "invocation_id_sha256": _hash("invocation"),
    }


def _host_exit_packet(contract: dict) -> dict:
    pointer = {
        "project_id": "book-faires",
        "accepted_pv": contract["accepted_pointer"]["accepted_pv"],
        "accepted_manifest_sha256": contract["accepted_pointer"][
            "manifest_sha256"
        ],
        "pointer_generation": contract["accepted_pointer"]["generation"],
        "prior_generation": contract["accepted_pointer"]["generation"] - 1,
    }
    packet = {
        "schema": "evidence-lane.host-exit-continuity-packet.v1",
        "packet_id": "hexit-test",
        "project_id": "book-faires",
        "evidence_session_id": contract["evidence_session_id"],
        "runtime_task_id": "runtime-task",
        "plan_task_id": contract["plan_task_id"],
        "active_plan": {
            "row": contract["active_plan_row"],
            "task_id": contract["plan_task_id"],
            "status": "in_progress",
            "lifecycle_status": "ACTIVE",
        },
        "route": {
            "interaction_profile": contract["interaction_profile"],
            "measured_server_filesystem": "EPHEMERAL_OR_UNAVAILABLE",
            "primary_runtime_authority": "CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
            "storage_connector_required": True,
            "google_drive_primary_runtime": False,
        },
        "pointer": pointer,
        "source_exit": {
            "exit_slip_sha256": _hash("exit-slip"),
            "lineage_event_sha256": _hash("lineage-event"),
            "reason": "STATELESS_EPHEMERAL_END",
            "lineage_head_sha256": _hash("lineage-event"),
        },
        "opaque_locator": "evi+host-exit://hexit-test",
        "opaque_locator_sha256": _hash("evi+host-exit://hexit-test"),
        "idempotency_key": _hash("host-exit-idempotency"),
        "persistence": {
            "state": "AWAITING_LATER_DURABLE_CONNECTOR_PERSISTENCE",
            "durable_persisted": False,
            "storage_receipt_sha256": None,
            "exit_complete": False,
            "continuity_claimed": False,
            "consumer_owner": "INDEPENDENT_HOST_ENTRY_CONTINUITY_ROW",
        },
        "authority_effects": {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "host_entry": "PENDING_LATER_EXACT_CONSUMER",
        },
        "candidate_promoted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "private_reasoning_stored": False,
        "sealed_at": "2026-08-13T12:00:00+00:00",
    }
    packet["packet_sha256"] = sha256_bytes(canonical_json_bytes(packet))
    return packet


def _consume(
    root: Path,
    envelope: dict,
    contract: dict,
    *,
    backend: InMemoryPersistence | None = None,
    candidate_sha256: str | None = None,
) -> dict:
    return consume_host_entry_envelope(
        root,
        envelope,
        expected_project_id="book-faires",
        expected_evidence_session_id=contract["evidence_session_id"],
        expected_plan_task_id=contract["plan_task_id"],
        expected_pointer=contract["accepted_pointer"],
        expected_active_plan_row=contract["active_plan_row"],
        expected_env_uop=contract["env_uop"],
        expected_worktree_sha256=contract["worktree"]["worktree_sha256"],
        expected_authority_heads=contract["authority_heads"],
        consumer_binding=_consumer(contract),
        consumed_at="2026-08-13T13:00:00+00:00",
        backend=backend,
        expected_candidate_overlay_sha256=candidate_sha256,
    )


def test_durable_local_uses_local_exact_once_ledger_without_connector(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book-faires"
    root.mkdir()
    contract = _contract(profile="DURABLE_LOCAL")
    issued = issue_host_entry_envelope(root, project_id="book-faires", **contract)

    consumed = _consume(root, issued["envelope"], contract)
    replay = _consume(root, issued["envelope"], contract)

    assert consumed["state"] == "CONSUMED"
    assert replay["state"] == "CONSUMED_IDEMPOTENT_REUSE"
    assert replay["receipt"] == consumed["receipt"]
    assert replay["mutations_replayed"] is False
    assert consumed["receipt"]["pointer_moved"] is False
    assert inspect_host_entry_continuity(
        root, project_id="book-faires"
    ) == {
        "status": "PASS",
        "project_id": "book-faires",
        "envelope_count": 1,
        "consumption_count": 1,
        "generation_roll_count": 0,
        "integrity": ["ok"],
        "foreign_key_errors": 0,
    }


@pytest.mark.parametrize("profile", ["INTERACTIVE_EPHEMERAL", "STATELESS_HEADLESS"])
def test_remote_entry_requires_persistence_and_transactional_exact_once(
    tmp_path: Path,
    profile: str,
) -> None:
    root = tmp_path / "book-faires"
    root.mkdir()
    contract = _contract(profile=profile)
    issued = issue_host_entry_envelope(root, project_id="book-faires", **contract)
    backend = InMemoryPersistence()

    persisted = persist_host_entry_envelope(issued["envelope"], backend=backend)
    consumed = _consume(root, issued["envelope"], contract, backend=backend)
    replay = _consume(root, issued["envelope"], contract, backend=backend)

    assert persisted["durable_persisted"] is True
    assert persisted["continuity_claimed"] is True
    assert consumed["state"] == "CONSUMED"
    assert replay["state"] == "CONSUMED_IDEMPOTENT_REUSE"
    assert replay["receipt"] == consumed["receipt"]
    assert len(backend.claims) == 1


def test_remote_entry_fails_without_transactional_connector(tmp_path: Path) -> None:
    root = tmp_path / "book-faires"
    root.mkdir()
    contract = _contract()
    envelope = issue_host_entry_envelope(
        root, project_id="book-faires", **contract
    )["envelope"]

    with pytest.raises(EvidenceLaneError) as blocked:
        _consume(root, envelope, contract)

    assert blocked.value.code == "HOST_ENTRY_TRANSACTIONAL_CONNECTOR_REQUIRED"


def test_remote_exit_is_complete_only_after_next_entry_persistence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book-faires"
    root.mkdir()
    contract = _contract()
    packet = _host_exit_packet(contract)
    backend = InMemoryPersistence()

    sealed = seal_next_host_entry_from_exit(
        root,
        packet,
        host_binding_id=contract["host_binding_id"],
        host_entry_ordinal=contract["host_entry_ordinal"],
        source_task=contract["source_task"],
        destination_task=contract["destination_task"],
        env_uop=contract["env_uop"],
        worktree=contract["worktree"],
        authority_heads=contract["authority_heads"],
        storage=contract["storage"],
        replay_nonce_sha256=contract["replay_nonce_sha256"],
        issued_at=contract["issued_at"],
        expires_at=contract["expires_at"],
        backend=backend,
    )

    assert sealed["completion"]["exit_complete"] is True
    assert sealed["completion"]["durable_persisted"] is True
    assert sealed["completion"]["source_exit_packet_mutated"] is False
    assert sealed["host_exit_packet"]["persistence"]["exit_complete"] is False
    assert sealed["completion"]["pointer_moved"] is False


def test_entry_rejects_expiry_generation_worktree_and_candidate_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book-faires"
    root.mkdir()
    candidate_sha = _hash("candidate")
    contract = _contract(
        candidate_overlay={
            "candidate_id": "candidate-1",
            "candidate_sha256": candidate_sha,
        }
    )
    envelope = issue_host_entry_envelope(
        root, project_id="book-faires", **contract
    )["envelope"]
    backend = InMemoryPersistence()
    persist_host_entry_envelope(envelope, backend=backend)

    with pytest.raises(EvidenceLaneError) as unauthorized_overlay:
        _consume(root, envelope, contract, backend=backend)
    assert unauthorized_overlay.value.code == "HOST_ENTRY_CANDIDATE_OVERLAY_UNAUTHORIZED"

    wrong_pointer = copy.deepcopy(contract)
    wrong_pointer["accepted_pointer"]["generation"] = 13
    with pytest.raises(EvidenceLaneError) as stale:
        _consume(
            root,
            envelope,
            wrong_pointer,
            backend=backend,
            candidate_sha256=candidate_sha,
        )
    assert stale.value.code == "HOST_ENTRY_BINDING_MISMATCH"

    wrong_worktree = copy.deepcopy(contract)
    wrong_worktree["worktree"]["worktree_sha256"] = _hash("other-worktree")
    with pytest.raises(EvidenceLaneError) as worktree:
        _consume(
            root,
            envelope,
            wrong_worktree,
            backend=backend,
            candidate_sha256=candidate_sha,
        )
    assert worktree.value.code == "HOST_ENTRY_BINDING_MISMATCH"

    with pytest.raises(EvidenceLaneError) as expired:
        consume_host_entry_envelope(
            root,
            envelope,
            expected_project_id="book-faires",
            expected_evidence_session_id=contract["evidence_session_id"],
            expected_plan_task_id=contract["plan_task_id"],
            expected_pointer=contract["accepted_pointer"],
            expected_active_plan_row=contract["active_plan_row"],
            expected_env_uop=contract["env_uop"],
            expected_worktree_sha256=contract["worktree"]["worktree_sha256"],
            expected_authority_heads=contract["authority_heads"],
            consumer_binding=_consumer(contract),
            consumed_at="2026-08-15T13:00:00+00:00",
            backend=backend,
            expected_candidate_overlay_sha256=candidate_sha,
        )
    assert expired.value.code == "HOST_ENTRY_ENVELOPE_EXPIRED"


def test_pointer_generation_roll_invalidates_old_and_reissues_active_binding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book-faires"
    root.mkdir()
    old_contract = _contract(generation=12, ordinal=1, profile="DURABLE_LOCAL")
    old = issue_host_entry_envelope(
        root, project_id="book-faires", **old_contract
    )["envelope"]
    new_contract = _contract(generation=13, ordinal=2, profile="DURABLE_LOCAL")

    rolled = roll_host_entry_generation(
        root,
        project_id="book-faires",
        accepted_pointer_generation=13,
        reissue_contracts=[new_contract],
        rolled_at="2026-08-13T12:30:00+00:00",
    )

    assert rolled["receipt"]["invalidated_count"] == 1
    assert len(rolled["receipt"]["reissued_envelope_ids"]) == 1
    with pytest.raises(EvidenceLaneError) as invalidated:
        _consume(root, old, old_contract)
    assert invalidated.value.code == "HOST_ENTRY_ENVELOPE_INVALIDATED_OR_UNKNOWN"
    new_envelope = rolled["reissued"][0]["envelope"]
    assert _consume(root, new_envelope, new_contract)["state"] == "CONSUMED"


def test_service_remote_resume_consumes_entry_before_runtime_continuity(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "evidence_lane_plugin.service.utc_now",
        lambda: "2026-08-13T13:00:00+00:00",
    )
    session_id, _candidate = build_and_approve_pv1(service)
    task = {
        "task_id": "remote-entry-row",
        "task_class": "add_bounded_feature",
        "requested_outcome": "Resume one exact stateless host invocation.",
        "permitted_paths": ["plugins/evidence-lane-plugin/**", "tests/**"],
        "permitted_tools": ["repository_read", "repository_write", "test"],
        "acceptance_checks": ["Exact host-entry receipt is consumed once."],
        "stop_condition": "Stop before HIL.",
    }
    final_hil = {
        **task,
        "task_id": "remote-entry-final-hil",
        "requested_outcome": "Present the physically final governed HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[task, final_hil],
        planned_by="human-test",
        plan_id="remote-entry-plan",
    )
    service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id=task["task_id"],
        session_id=session_id,
        contract=task,
    )
    project_root = service.store.project_root("book-faires")
    pointer = service.store.pointer("book-faires")
    pointer_body = pointer.as_dict()
    accepted_pointer = {
        "accepted_pv": pointer.accepted_pv,
        "generation": pointer.generation,
        "manifest_sha256": pointer.accepted_manifest_sha256,
        "pointer_sha256": sha256_bytes(canonical_json_bytes(pointer_body)),
    }
    project_lineage = ProjectChatLineage(project_root / "lineage").sync()
    heads = derive_host_entry_authority_heads(
        project_root,
        project_id="book-faires",
        accepted_pointer=pointer_body,
        chat_lineage_head_sha256=project_lineage["head_state_sha256"],
    )
    flash = service.flash_authority.ensure_flashed()
    env_uop = derive_host_entry_env_uop(flash)
    active_row = next(
        row
        for row in service.store.backlog_status("book-faires")["goal_projection"][
            "rows"
        ]
        if row["status"] == "in_progress"
    )
    destination_task_id = "host-session-remote-entry"
    envelope = issue_host_entry_envelope(
        project_root,
        project_id="book-faires",
        evidence_session_id=session_id,
        plan_task_id=task["task_id"],
        host_binding_id="remote-host-binding",
        host_entry_ordinal=1,
        interaction_profile="STATELESS_HEADLESS",
        accepted_pointer=accepted_pointer,
        active_plan_row=active_row["number"],
        source_task={
            "task_id": "source-task",
            "task_deep_link_sha256": _hash("codex://threads/source-task"),
        },
        destination_task={
            "task_id": destination_task_id,
            "task_deep_link_sha256": _hash(
                f"codex://threads/{destination_task_id}"
            ),
        },
        env_uop=env_uop,
        worktree={
            "source_identity_sha256": _hash("source-identity"),
            "worktree_sha256": calculate_worktree_sha256(
                service.store.config("book-faires").repository_path
            ),
            "status_sha256": _hash("dirty-status"),
            "dirty_untracked_bytes_preserved": True,
        },
        authority_heads=heads,
        previous_exit_sha256=_hash("previous-exit"),
        storage={
            "mode": "configured_durable_connector",
            "connector_id": "transactional-test",
            "selection_receipt_sha256": _hash("selection"),
            "transactional_exact_once_required": True,
            "google_drive_primary_runtime": False,
        },
        replay_nonce_sha256=_hash("resume-nonce"),
        issued_at="2026-08-13T12:00:00+00:00",
        expires_at="2026-08-14T12:00:00+00:00",
    )["envelope"]
    backend = InMemoryPersistence()
    persist_host_entry_envelope(envelope, backend=backend)
    service.sync_service = PVSyncService(store=service.store, backend=backend)

    resumed = service.resume_session(
        project_id="book-faires",
        host="CODEX_VM",
        host_session_id=destination_task_id,
        ephemeral=True,
        client_can_edit_source=True,
        server_has_durable_filesystem=False,
        runtime_context={
            "interaction_profile": "HEADLESS_API",
            "account_tier": "API",
            "host_entry_envelope": envelope,
            "host_entry_invocation_id": "remote-invocation-01",
            "host_surface": {
                "container_channel": "CHATGPT_DESKTOP_BETA",
                "container_version": "26.813.1-beta",
                "active_surface": "CODEX",
                "active_surface_evidence": "HOST_SESSION_TASK_CAPABILITY_RECEIPT",
                "workspace_class": "EPHEMERAL_REMOTE_WORKSPACE",
            },
            "execution_profile": {
                "model": "gpt-5.6-sol",
                "submodel": "sol",
                "reasoning_effort": "ultra",
                "reasoning_speed": "standard",
            },
            "native_capabilities": {
                "exact_task_binding": True,
                "goal": True,
                "hooks": True,
                "host_plan": True,
                "local_filesystem": False,
            },
            "required_native_capabilities": ["exact_task_binding", "host_plan"],
        },
    )

    assert resumed["runtime_continuity"]["host_entry"]["state"] == (
        "CONSUMED_EXACT_ONCE_FROM_TRANSACTIONAL_CONNECTOR"
    )
    assert resumed["runtime_continuity"]["host_entry"][
        "consumption_receipt_sha256"
    ]
    assert resumed["runtime_continuity"]["invocation"]["headless_api"] is True
    assert resumed["runtime_continuity"]["invocation"][
        "tunnel_requirement"
    ] == "NOT_REQUIRED_FOR_API_LAYER"


def test_service_remote_boot_cannot_invent_a_session_from_connector_only(
    service,
) -> None:
    service.sync_service = PVSyncService(
        store=service.store,
        backend=InMemoryPersistence(),
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        service.boot_session(
            project_id="book-faires",
            user_id="remote-user",
            workspace_id="remote-workspace",
            host="CODEX_VM",
            agent_id="remote-agent",
            sandbox_id="remote-sandbox",
            ephemeral=True,
            runtime_context={
                "interaction_profile": "HEADLESS_API",
                "host_surface": {
                    "container_channel": "CHATGPT_DESKTOP_BETA",
                    "container_version": "26.813.1-beta",
                    "active_surface": "CODEX",
                    "active_surface_evidence": (
                        "HOST_SESSION_TASK_CAPABILITY_RECEIPT"
                    ),
                    "workspace_class": "EPHEMERAL_REMOTE_WORKSPACE",
                },
                "execution_profile": {
                    "model": "gpt-5.6-sol",
                    "submodel": "sol",
                    "reasoning_effort": "ultra",
                    "reasoning_speed": "standard",
                },
                "native_capabilities": {
                    "exact_task_binding": True,
                    "host_plan": True,
                },
                "required_native_capabilities": [
                    "exact_task_binding",
                    "host_plan",
                ],
            },
            host_session_id="remote-host-session",
            client_can_edit_source=True,
            server_has_durable_filesystem=False,
        )

    assert blocked.value.code == (
        "HOST_ENTRY_REMOTE_BOOT_REQUIRES_EXACT_SESSION_RECOVERY"
    )


@pytest.mark.parametrize(
    (
        "host",
        "ephemeral",
        "durable",
        "profile",
        "native_mcp",
        "expected_tunnel",
    ),
    [
        (
            "CODEX_DESKTOP",
            False,
            True,
            "CODEX_APP_INTERACTIVE",
            True,
            "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        ),
        (
            "CODEX_DESKTOP",
            False,
            True,
            "CODEX_APP_INTERACTIVE",
            False,
            "REQUIRED_FOR_HOST_TOOL_GAP",
        ),
        (
            "CODEX_CLI",
            False,
            True,
            "CODEX_CLI_NATIVE",
            False,
            "REQUIRED_FOR_HOST_TOOL_GAP",
        ),
        (
            "CODEX_VM",
            True,
            False,
            "CODEX_APP_INTERACTIVE",
            False,
            "REQUIRED_FOR_HOST_TOOL_GAP",
        ),
        (
            "CODEX_VM",
            True,
            False,
            "HEADLESS_API",
            False,
            "NOT_REQUIRED_FOR_API_LAYER",
        ),
    ],
)
def test_host_account_api_storage_tunnel_axes_remain_independent(
    host: str,
    ephemeral: bool,
    durable: bool,
    profile: str,
    native_mcp: bool,
    expected_tunnel: str,
) -> None:
    plus = route_persistence(
        host,
        ephemeral=ephemeral,
        server_has_durable_filesystem=durable,
        runtime_context={
            "interaction_profile": profile,
            "account_tier": "PLUS",
            "native_capabilities": {"native_mcp": native_mcp},
        },
    )
    api = route_persistence(
        host,
        ephemeral=ephemeral,
        server_has_durable_filesystem=durable,
        runtime_context={
            "interaction_profile": profile,
            "account_tier": "API",
            "native_capabilities": {"native_mcp": native_mcp},
        },
    )

    assert plus.tunnel_requirement == api.tunnel_requirement == expected_tunnel
    assert plus.primary_runtime_authority == api.primary_runtime_authority
    assert plus.account_tier_affects_routing is False
    assert plus.api_billing_affects_routing is False
    assert api.account_tier_affects_routing is False
    assert api.api_billing_affects_routing is False
