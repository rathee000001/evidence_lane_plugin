from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.canon_task_graph import (
    CANON_DISPATCH_RECEIPT_SCHEMA_V2,
    CODEX_HOST_CREATE_CAPABILITY,
    CODEX_HOST_CREATE_RECEIPT_SCHEMA,
    CodexHostDispatcher,
    bind_received_canon_task_edge,
    decide_canon_input,
    dispatch_linked_canon_task,
    inspect_canon_inbox,
    inspect_canon_task_graph,
    raise_canon_backfire,
    receive_canon_envelope,
    register_canon_task_edge,
    register_expected_canon_contract,
    restore_canon_state_travel_continuity,
    seal_canon_envelope,
    seal_canon_state_travel_continuity,
    seal_canon_task_result,
    supersede_canon_input,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from jsonschema import Draft202012Validator

SOURCE_PROJECT = "canon-source"
DESTINATION_PROJECT = "canon-destination"
CREATED_AT = "2026-08-13T12:00:00Z"
RECEIVED_AT = "2026-08-13T12:01:00Z"
DECIDED_AT = "2026-08-13T12:02:00Z"
EXPIRES_AT = "2026-08-14T12:00:00Z"


def _hash(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _root(tmp_path: Path, project_id: str) -> Path:
    root = tmp_path / project_id
    root.mkdir(exist_ok=True)
    return root


def _endpoint(project_id: str, task: str) -> dict[str, str]:
    return {
        "project_id": project_id,
        "task_uuid": f"task-{task}",
        "task_deep_link": f"codex://tasks/task-{task}",
        "lane_id": "PLAN_LANE",
        "delta_id": f"EL-{task.upper()}",
        "session_id": f"session-{task}",
    }


def _pointer(project_id: str, *, pv_ref: str = "PV2", generation: int = 2) -> dict:
    return {
        "project_id": project_id,
        "pv_ref": pv_ref,
        "generation": generation,
        "manifest_sha256": _hash(f"{project_id}:manifest:{generation}"),
        "package_sha256": _hash(f"{project_id}:package:{generation}"),
    }


def _contract(
    destination_root: Path,
    *,
    destination_project: str,
    source: dict,
    destination: dict,
    source_pointer: dict,
    schema_id: str = "canon.plan-steer.v1",
    schema_sha256: str | None = None,
    canon_types: list[str] | None = None,
    authorities: list[str] | None = None,
    payload_keys: list[str] | None = None,
    permitted_actions: list[str] | None = None,
    expected_return_contract_sha256: str | None = None,
    expires_at: str = EXPIRES_AT,
) -> dict:
    return register_expected_canon_contract(
        destination_root,
        project_id=destination_project,
        contract={
            "contract_id": f"contract:{destination['task_uuid']}:{schema_id}",
            "contract_version": 1,
            "active": True,
            "destination": destination,
            "source_allowlist": [source],
            "accepted_source_pointers": [source_pointer],
            "schema_id": schema_id,
            "schema_version": "1",
            "schema_sha256": schema_sha256 or _hash(schema_id),
            "canon_types": canon_types or ["PLAN_STEER"],
            "authority_requested": authorities or ["PLAN_STEER"],
            "permitted_payload_keys": payload_keys or ["requirements"],
            "permitted_actions": permitted_actions or ["READ_EVIDENCE"],
            "expected_return_contract_sha256": expected_return_contract_sha256,
            "independent_hil_owner_task_uuid": destination["task_uuid"],
            "expires_at": expires_at,
        },
    )["contract"]


def _seal(
    source_root: Path,
    *,
    source_project: str,
    source: dict,
    destination: dict,
    source_pointer: dict,
    contract_sha256: str,
    revision: int = 1,
    supersedes: str | None = None,
    idempotency_key: str = "canon-fixture-001",
    payload: dict | None = None,
    canon_type: str = "PLAN_STEER",
    authority_requested: str = "PLAN_STEER",
    schema_id: str = "canon.plan-steer.v1",
    schema_sha256: str | None = None,
    expected_return_contract_sha256: str | None = None,
    permitted_actions: list[str] | None = None,
) -> dict:
    return seal_canon_envelope(
        source_root,
        project_id=source_project,
        source=source,
        destination=destination,
        direction="DOWNSTREAM",
        canon_type=canon_type,
        authority_requested=authority_requested,
        contract_id="contract:fixture",
        contract_version=1,
        destination_contract_sha256=contract_sha256,
        schema_id=schema_id,
        schema_version="1",
        schema_sha256=schema_sha256 or _hash(schema_id),
        source_pointer=source_pointer,
        evidence_refs=[{"ref": "file:research/fixture.md", "sha256": _hash("evidence")}],
        payload=payload or {"requirements": ["bounded"]},
        permitted_actions=permitted_actions or ["READ_EVIDENCE"],
        dependency_ids=["EL-DEPENDENCY"],
        expected_return_contract_sha256=expected_return_contract_sha256,
        independent_hil_owner_task_uuid=destination["task_uuid"],
        revision=revision,
        idempotency_key=idempotency_key,
        created_at=CREATED_AT,
        expires_at=EXPIRES_AT,
        supersedes=supersedes,
    )["envelope"]


def _error_code(exc: pytest.ExceptionInfo[EvidenceLaneError]) -> str:
    return exc.value.code


def _schema(name: str) -> dict:
    path = (
        Path(__file__).parents[1]
        / "plugins"
        / "evidence-lane-plugin"
        / "schemas"
        / name
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_expected_packet_auto_admits_without_cross_plane_effects(tmp_path: Path) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    pointer = _pointer(SOURCE_PROJECT)
    contract = _contract(
        destination_root,
        destination_project=DESTINATION_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
    )
    envelope = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        contract_sha256=contract["contract_sha256"],
    )

    received = receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=envelope,
        received_at=RECEIVED_AT,
    )
    replay = receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=envelope,
        received_at=RECEIVED_AT,
    )
    inbox = inspect_canon_inbox(
        destination_root,
        project_id=DESTINATION_PROJECT,
        task_uuid=destination["task_uuid"],
        states=["EXPECTED_ADMITTED"],
        limit=20,
    )

    assert received["state"] == "EXPECTED_ADMITTED"
    assert received["canon_input_hil_required"] is False
    assert replay["idempotent_reuse"] is True
    assert inbox["packet_count"] == 1
    assert received["authority_effects"]["project_truth"] == "NONE"
    assert received["authority_effects"]["agent_learning"] == "NONE"
    assert received["project_hil_invoked"] is False
    assert received["learning_hil_invoked"] is False


def test_packaged_canon_schemas_are_valid_and_accept_sealed_envelope(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    envelope = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=_pointer(SOURCE_PROJECT),
        contract_sha256=_hash("undefined"),
    )
    envelope_schema = _schema("canon-envelope.schema.json")
    contract_schema = _schema("canon-expected-contract.schema.json")
    edge_schema = _schema("canon-task-edge.schema.json")

    Draft202012Validator.check_schema(envelope_schema)
    Draft202012Validator.check_schema(contract_schema)
    Draft202012Validator.check_schema(edge_schema)
    Draft202012Validator(envelope_schema).validate(envelope)


def test_contract_version_is_immutable_and_rejects_changed_bytes(tmp_path: Path) -> None:
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    pointer = _pointer(SOURCE_PROJECT)
    first = _contract(
        destination_root,
        destination_project=DESTINATION_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
    )
    assert first["contract_version"] == 1

    with pytest.raises(EvidenceLaneError) as conflict:
        _contract(
            destination_root,
            destination_project=DESTINATION_PROJECT,
            source=source,
            destination=destination,
            source_pointer=pointer,
            payload_keys=["requirements", "changed"],
        )
    assert _error_code(conflict) == "CANON_CONTRACT_VERSION_IMMUTABILITY_CONFLICT"
    assert len(list((destination_root / "canon" / "contracts").glob("*.json"))) == 1


def test_expired_expected_contract_cannot_auto_admit(tmp_path: Path) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    pointer = _pointer(SOURCE_PROJECT)
    contract = _contract(
        destination_root,
        destination_project=DESTINATION_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        expires_at="2026-08-13T12:00:30Z",
    )
    envelope = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        contract_sha256=contract["contract_sha256"],
    )
    result = receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=envelope,
        received_at=RECEIVED_AT,
    )

    assert result["state"] == "PENDING_HIL"
    assert "EXPECTED_CONTRACT_EXPIRED" in result["classification"]["reasons"]


def test_undefined_packet_requires_receiver_owned_exact_three_way_hil(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    envelope = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=_pointer(SOURCE_PROJECT),
        contract_sha256=_hash("undefined-contract"),
    )
    received = receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=envelope,
        received_at=RECEIVED_AT,
    )
    assert received["state"] == "PENDING_HIL"
    assert received["canon_input_hil_required"] is True

    with pytest.raises(EvidenceLaneError) as wrong_owner:
        decide_canon_input(
            destination_root,
            project_id=DESTINATION_PROJECT,
            canon_id=envelope["canon_id"],
            expected_canon_sha256=envelope["canon_sha256"],
            decision_token="ACCEPT",
            actor_task_uuid="task-wrong",
            actor_id="user",
            decided_at=DECIDED_AT,
        )
    assert _error_code(wrong_owner) == "CANON_DECISION_OWNER_MISMATCH"

    accepted = decide_canon_input(
        destination_root,
        project_id=DESTINATION_PROJECT,
        canon_id=envelope["canon_id"],
        expected_canon_sha256=envelope["canon_sha256"],
        decision_token="ACCEPT",
        actor_task_uuid=destination["task_uuid"],
        actor_id="user",
        decided_at=DECIDED_AT,
    )
    replay = decide_canon_input(
        destination_root,
        project_id=DESTINATION_PROJECT,
        canon_id=envelope["canon_id"],
        expected_canon_sha256=envelope["canon_sha256"],
        decision_token="ACCEPT",
        actor_task_uuid=destination["task_uuid"],
        actor_id="user",
        decided_at=DECIDED_AT,
    )
    assert accepted["receipt"]["state_after"] == "ACCEPTED_INPUT"
    assert accepted["receipt"]["project_truth_pointer_moved"] is False
    assert accepted["receipt"]["learning_pointer_moved"] is False
    assert replay["idempotent_reuse"] is True

    with pytest.raises(EvidenceLaneError) as changed_replay:
        decide_canon_input(
            destination_root,
            project_id=DESTINATION_PROJECT,
            canon_id=envelope["canon_id"],
            expected_canon_sha256=envelope["canon_sha256"],
            decision_token="ACCEPT",
            actor_task_uuid=destination["task_uuid"],
            actor_id="another-user",
            decided_at=DECIDED_AT,
        )
    assert _error_code(changed_replay) == "CANON_DECISION_STATE_INVALID"


@pytest.mark.parametrize("decision", ["REJECT", "MORE_RESEARCH"])
def test_reject_and_more_research_preserve_bounded_receipts(
    tmp_path: Path, decision: str
) -> None:
    source_root = _root(tmp_path, f"{SOURCE_PROJECT}-{decision.lower()}")
    destination_root = _root(tmp_path, f"{DESTINATION_PROJECT}-{decision.lower()}")
    source_project = source_root.name
    destination_project = destination_root.name
    source = _endpoint(source_project, f"source-{decision.lower()}")
    destination = _endpoint(destination_project, f"destination-{decision.lower()}")
    envelope = _seal(
        source_root,
        source_project=source_project,
        source=source,
        destination=destination,
        source_pointer=_pointer(source_project),
        contract_sha256=_hash(f"undefined-{decision}"),
        idempotency_key=f"canon-{decision.lower()}",
    )
    receive_canon_envelope(
        destination_root,
        project_id=destination_project,
        envelope=envelope,
        received_at=RECEIVED_AT,
    )
    result = decide_canon_input(
        destination_root,
        project_id=destination_project,
        canon_id=envelope["canon_id"],
        expected_canon_sha256=envelope["canon_sha256"],
        decision_token=decision,
        actor_task_uuid=destination["task_uuid"],
        actor_id="user",
        decided_at=DECIDED_AT,
        reason="Need a bounded correction",
        research_request=(
            {"requested_fields": ["missing_hash"], "max_revisions": 1}
            if decision == "MORE_RESEARCH"
            else None
        ),
    )

    assert result["receipt"]["state_after"] == (
        "REJECTED" if decision == "REJECT" else "MORE_RESEARCH"
    )
    assert result["receipt"]["source_notification_required"] is True
    assert result["receipt"]["source_write_authority_granted"] is False


def test_more_research_requires_new_revision_and_supersedes_history(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    pointer = _pointer(SOURCE_PROJECT)
    first = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        contract_sha256=_hash("undefined-research"),
    )
    receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=first,
        received_at=RECEIVED_AT,
    )
    decide_canon_input(
        destination_root,
        project_id=DESTINATION_PROJECT,
        canon_id=first["canon_id"],
        expected_canon_sha256=first["canon_sha256"],
        decision_token="MORE_RESEARCH",
        actor_task_uuid=destination["task_uuid"],
        actor_id="user",
        decided_at=DECIDED_AT,
        reason="Hash is missing",
        research_request={"requested_fields": ["missing_hash"]},
    )
    revised = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        contract_sha256=_hash("undefined-research"),
        revision=2,
        supersedes=first["canon_id"],
        idempotency_key="canon-fixture-revision-002",
        payload={"requirements": ["bounded"], "missing_hash": _hash("now-present")},
    )
    result = receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=revised,
        received_at="2026-08-13T12:03:00Z",
    )
    inbox = inspect_canon_inbox(
        destination_root,
        project_id=DESTINATION_PROJECT,
        task_uuid=destination["task_uuid"],
        states=None,
        limit=20,
    )

    assert result["state"] == "PENDING_HIL"
    states = {packet["canon_id"]: packet["state"] for packet in inbox["packets"]}
    assert states[first["canon_id"]] == "SUPERSEDED"
    assert states[revised["canon_id"]] == "PENDING_HIL"

    decide_canon_input(
        destination_root,
        project_id=DESTINATION_PROJECT,
        canon_id=revised["canon_id"],
        expected_canon_sha256=revised["canon_sha256"],
        decision_token="MORE_RESEARCH",
        actor_task_uuid=destination["task_uuid"],
        actor_id="user",
        decided_at="2026-08-13T12:04:00Z",
        reason="One more bounded field is required",
        research_request={"requested_fields": ["second_hash"]},
    )
    third = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        contract_sha256=_hash("undefined-research"),
        revision=3,
        supersedes=revised["canon_id"],
        idempotency_key="canon-fixture-revision-003",
        payload={"requirements": ["bounded"], "second_hash": _hash("second")},
    )
    receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=third,
        received_at="2026-08-13T12:05:00Z",
    )
    replay = supersede_canon_input(
        destination_root,
        project_id=DESTINATION_PROJECT,
        canon_id=first["canon_id"],
        superseded_by=revised["canon_id"],
        occurred_at="2026-08-13T12:06:00Z",
    )
    assert replay["idempotent_reuse"] is True
    with pytest.raises(EvidenceLaneError) as conflict:
        supersede_canon_input(
            destination_root,
            project_id=DESTINATION_PROJECT,
            canon_id=first["canon_id"],
            superseded_by=third["canon_id"],
            occurred_at="2026-08-13T12:06:00Z",
        )
    assert _error_code(conflict) == "CANON_SUPERSESSION_REPLAY_CONFLICT"


class _Dispatcher:
    def __init__(self, destination: dict) -> None:
        self.destination = destination
        self.calls = 0
        self.seen: set[str] = set()
        self.adapter = CodexHostDispatcher(self._create)
        self.host_kind = self.adapter.host_kind
        self.capability = self.adapter.capability

    def _create(self, request: dict) -> dict:
        self.calls += 1
        key = str(request["idempotency_key"])
        replayed = key in self.seen
        self.seen.add(key)
        body = {
            "schema": CODEX_HOST_CREATE_RECEIPT_SCHEMA,
            "host_kind": "CODEX",
            "operation": "CREATE_LINKED_TASK",
            "capability": CODEX_HOST_CREATE_CAPABILITY,
            "idempotency_key": key,
            "request_sha256": request["request_sha256"],
            "destination": self.destination,
            "created_once": True,
            "replayed": replayed,
            "host_receipt_id": f"host_{key}",
            "issued_at": CREATED_AT,
        }
        return {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    def create_linked_task(self, request: dict) -> dict:
        return dict(self.adapter.create_linked_task(request))


def _dispatch_kwargs(source: dict) -> dict:
    return {
        "project_id": SOURCE_PROJECT,
        "source": source,
        "task_title": "Bounded destination",
        "task_mode": "TOP_LEVEL_TASK",
        "scope_class": "READ_ONLY",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "user_subagent_authorized": False,
        "host_write_authorization_sha256": None,
        "direction": "DOWNSTREAM",
        "contract_sha256": _hash("host-dispatch-contract"),
        "schema_sha256": _hash("host-dispatch-schema"),
        "edge_revision": 1,
        "permitted_actions": ["READ_EVIDENCE"],
        "dependency_ids": [],
        "expected_return_contract_sha256": _hash("host-dispatch-return"),
        "expires_at": EXPIRES_AT,
        "requested_at": CREATED_AT,
    }


def test_codex_dispatch_returns_native_host_action_then_binds_exact_receipt(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    kwargs = _dispatch_kwargs(_endpoint(SOURCE_PROJECT, "source"))
    pending = dispatch_linked_canon_task(
        source_root,
        dispatcher=None,
        **kwargs,
    )
    assert pending["status"] == "HOST_ACTION_REQUIRED"
    assert pending["created_or_bound"] is False
    assert pending["authority_before"] == pending["authority_after"]
    assert pending["host_request"]["idempotency_key"] == pending["dispatch_id"]

    destination = _endpoint(DESTINATION_PROJECT, "destination")
    receipt_body = {
        "schema": CODEX_HOST_CREATE_RECEIPT_SCHEMA,
        "host_kind": "CODEX",
        "operation": "CREATE_LINKED_TASK",
        "capability": CODEX_HOST_CREATE_CAPABILITY,
        "idempotency_key": pending["dispatch_id"],
        "request_sha256": pending["request_sha256"],
        "destination": destination,
        "created_once": True,
        "replayed": False,
        "host_receipt_id": "host_native_two_phase",
        "issued_at": CREATED_AT,
    }
    host_receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    completed = dispatch_linked_canon_task(
        source_root,
        dispatcher=None,
        host_creation_receipt=host_receipt,
        **kwargs,
    )
    assert completed["status"] == "PASS"
    assert completed["receipt"]["host_creation_receipt"] == host_receipt
    assert completed["edge"]["destination"] == destination


def test_codex_dispatch_rejects_a_mismatched_host_receipt(tmp_path: Path) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination = _endpoint(DESTINATION_PROJECT, "destination")

    def mismatched(request: dict) -> dict:
        body = {
            "schema": CODEX_HOST_CREATE_RECEIPT_SCHEMA,
            "host_kind": "CODEX",
            "operation": "CREATE_LINKED_TASK",
            "capability": CODEX_HOST_CREATE_CAPABILITY,
            "idempotency_key": "wrong-idempotency-key",
            "request_sha256": request["request_sha256"],
            "destination": destination,
            "created_once": True,
            "replayed": False,
            "host_receipt_id": "host_wrong",
            "issued_at": CREATED_AT,
        }
        return {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }

    with pytest.raises(EvidenceLaneError) as mismatch:
        dispatch_linked_canon_task(
            source_root,
            dispatcher=CodexHostDispatcher(mismatched),
            **_dispatch_kwargs(_endpoint(SOURCE_PROJECT, "source")),
        )
    assert _error_code(mismatch) == "CANON_CODEX_HOST_RECEIPT_BINDING_MISMATCH"


def test_codex_host_adapter_reuses_the_stable_creation_key() -> None:
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    dispatcher = _Dispatcher(destination)
    request = {
        "schema": "evidence-lane.codex-host-linked-task-create-request.v1",
        "host_kind": "CODEX",
        "operation": "CREATE_LINKED_TASK",
        "capability": CODEX_HOST_CREATE_CAPABILITY,
        "idempotency_key": "cdispatch_stable",
        "request_sha256": _hash("stable-host-request"),
        "required_receipt_schema": CODEX_HOST_CREATE_RECEIPT_SCHEMA,
        "canon_request": {},
    }

    first = dispatcher.create_linked_task(request)
    second = dispatcher.create_linked_task(request)

    assert dispatcher.calls == 2
    assert first["task_uuid"] == second["task_uuid"] == destination["task_uuid"]
    assert first["task_deep_link"] == second["task_deep_link"]
    assert first["host_creation_receipt"]["replayed"] is False
    assert second["host_creation_receipt"]["replayed"] is True
    assert first["host_creation_receipt"]["host_receipt_id"] == second[
        "host_creation_receipt"
    ]["host_receipt_id"]


def test_cross_project_dispatch_edge_binding_and_typed_result_round_trip(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    destination_pointer = _pointer(DESTINATION_PROJECT, pv_ref="PV3", generation=3)
    result_schema = "canon.task-result.v1"
    result_schema_sha256 = _hash(result_schema)
    return_contract = _contract(
        source_root,
        destination_project=SOURCE_PROJECT,
        source=destination,
        destination=source,
        source_pointer=destination_pointer,
        schema_id=result_schema,
        schema_sha256=result_schema_sha256,
        canon_types=["TASK_RESULT"],
        authorities=["TASK_RESULT"],
        payload_keys=[
            "edge_id",
            "summary",
            "local_project_hil_decision_propagated",
            "local_learning_hil_decision_propagated",
            "local_pointer_state_propagated",
            "source_write_authority_propagated",
        ],
        permitted_actions=["REPORT_RESULT"],
    )
    dispatcher = _Dispatcher(destination)
    dispatch = dispatch_linked_canon_task(
        source_root,
        project_id=SOURCE_PROJECT,
        source=source,
        dispatcher=dispatcher,
        task_title="Bounded destination",
        task_mode="TOP_LEVEL_TASK",
        scope_class="READ_ONLY",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        user_subagent_authorized=False,
        host_write_authorization_sha256=None,
        direction="DOWNSTREAM",
        contract_sha256=_hash("destination-input-contract"),
        schema_sha256=_hash("destination-input-schema"),
        edge_revision=1,
        permitted_actions=["READ_EVIDENCE"],
        dependency_ids=["EL-SOURCE"],
        expected_return_contract_sha256=return_contract["contract_sha256"],
        expires_at=EXPIRES_AT,
        requested_at=CREATED_AT,
    )
    replay = dispatch_linked_canon_task(
        source_root,
        project_id=SOURCE_PROJECT,
        source=source,
        dispatcher=None,
        task_title="Bounded destination",
        task_mode="TOP_LEVEL_TASK",
        scope_class="READ_ONLY",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        user_subagent_authorized=False,
        host_write_authorization_sha256=None,
        direction="DOWNSTREAM",
        contract_sha256=_hash("destination-input-contract"),
        schema_sha256=_hash("destination-input-schema"),
        edge_revision=1,
        permitted_actions=["READ_EVIDENCE"],
        dependency_ids=["EL-SOURCE"],
        expected_return_contract_sha256=return_contract["contract_sha256"],
        expires_at=EXPIRES_AT,
        requested_at=CREATED_AT,
    )
    bind_received_canon_task_edge(
        destination_root,
        project_id=DESTINATION_PROJECT,
        edge=dispatch["edge"],
    )
    sealed_result = seal_canon_task_result(
        destination_root,
        project_id=DESTINATION_PROJECT,
        edge_id=dispatch["edge"]["edge_id"],
        source_pointer=destination_pointer,
        evidence_refs=[{"ref": "file:result.json", "sha256": _hash("result")}],
        result_payload={"summary": "bounded result"},
        schema_id=result_schema,
        schema_version="1",
        schema_sha256=result_schema_sha256,
        created_at=CREATED_AT,
        expires_at=EXPIRES_AT,
    )
    received = receive_canon_envelope(
        source_root,
        project_id=SOURCE_PROJECT,
        envelope=sealed_result["envelope"],
        received_at=RECEIVED_AT,
    )
    graph = inspect_canon_task_graph(source_root, project_id=SOURCE_PROJECT)

    assert dispatcher.calls == 1
    assert replay["idempotent_reuse"] is True
    assert dispatch["receipt"]["schema"] == CANON_DISPATCH_RECEIPT_SCHEMA_V2
    assert dispatch["receipt"]["host_creation_receipt"]["destination"] == destination
    assert received["state"] == "EXPECTED_ADMITTED"
    assert graph["missing_returns"] == []
    assert sealed_result["envelope"]["project_truth_pointer_moved"] is False
    assert sealed_result["envelope"]["learning_pointer_moved"] is False


def test_subagent_dispatch_requires_user_authority_and_forbids_hil_tools(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    dispatcher = _Dispatcher(_endpoint(DESTINATION_PROJECT, "destination"))
    kwargs = {
        "project_id": SOURCE_PROJECT,
        "source": source,
        "dispatcher": dispatcher,
        "task_title": "Bounded subagent",
        "task_mode": "SUBAGENT",
        "scope_class": "READ_ONLY",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "host_write_authorization_sha256": None,
        "direction": "DOWNSTREAM",
        "contract_sha256": _hash("contract"),
        "schema_sha256": _hash("schema"),
        "edge_revision": 1,
        "permitted_actions": ["READ_EVIDENCE"],
        "dependency_ids": [],
        "expected_return_contract_sha256": _hash("return"),
        "expires_at": EXPIRES_AT,
        "requested_at": CREATED_AT,
    }
    with pytest.raises(EvidenceLaneError) as denied:
        dispatch_linked_canon_task(
            source_root, user_subagent_authorized=False, **kwargs
        )
    assert _error_code(denied) == "CANON_SUBAGENT_USER_AUTHORITY_REQUIRED"

    kwargs["permitted_tools"] = ["repository_read", "pv_fuse"]
    with pytest.raises(EvidenceLaneError) as forbidden:
        dispatch_linked_canon_task(
            source_root, user_subagent_authorized=True, **kwargs
        )
    assert _error_code(forbidden) == "CANON_SUBAGENT_HIL_OR_LIFECYCLE_TOOL_FORBIDDEN"
    assert dispatcher.calls == 0


def test_governed_read_write_link_requires_external_host_receipt(tmp_path: Path) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    dispatcher = _Dispatcher(_endpoint(DESTINATION_PROJECT, "destination"))
    kwargs = {
        "project_id": SOURCE_PROJECT,
        "source": source,
        "dispatcher": dispatcher,
        "task_title": "Governed writer",
        "task_mode": "TOP_LEVEL_TASK",
        "scope_class": "GOVERNED_READ_WRITE",
        "permitted_paths": ["bounded/**"],
        "permitted_tools": ["repository_read", "repository_write"],
        "user_subagent_authorized": False,
        "direction": "DOWNSTREAM",
        "contract_sha256": _hash("contract"),
        "schema_sha256": _hash("schema"),
        "edge_revision": 1,
        "permitted_actions": ["READ_EVIDENCE"],
        "dependency_ids": [],
        "expected_return_contract_sha256": _hash("return"),
        "expires_at": EXPIRES_AT,
        "requested_at": CREATED_AT,
    }
    with pytest.raises(EvidenceLaneError) as denied:
        dispatch_linked_canon_task(
            source_root, host_write_authorization_sha256=None, **kwargs
        )
    assert _error_code(denied) == "CANON_LINKED_TASK_WRITE_AUTHORITY_REQUIRED"
    assert dispatcher.calls == 0

    allowed = dispatch_linked_canon_task(
        source_root,
        host_write_authorization_sha256=_hash("host-write-receipt"),
        **kwargs,
    )
    assert dispatcher.calls == 1
    assert allowed["edge"]["source_write_authority_granted"] is False
    assert allowed["edge"]["source_write_authority_source"] == "HOST_TASK_CONTRACT"


def test_task_graph_rejects_cycles(tmp_path: Path) -> None:
    root = _root(tmp_path, SOURCE_PROJECT)
    first = _endpoint(SOURCE_PROJECT, "one")
    second = _endpoint(SOURCE_PROJECT, "two")

    def edge(edge_id: str, source: dict, destination: dict) -> dict:
        return {
            "edge_id": edge_id,
            "source": source,
            "destination": destination,
            "direction": "LATERAL",
            "contract_sha256": _hash(f"{edge_id}:contract"),
            "schema_sha256": _hash(f"{edge_id}:schema"),
            "edge_revision": 1,
            "permitted_actions": ["READ_EVIDENCE"],
            "dependency_ids": [],
            "expected_return_contract_sha256": _hash(f"{edge_id}:return"),
            "independent_hil_owner_task_uuid": destination["task_uuid"],
            "task_mode": "TOP_LEVEL_TASK",
            "scope_class": "READ_ONLY",
            "permitted_paths": [],
            "permitted_tools": ["repository_read"],
            "one_writer": True,
            "subagent_hil_allowed": False,
            "approval_propagation_allowed": False,
            "pointer_propagation_allowed": False,
            "source_write_authority_granted": False,
            "source_write_authority_source": "NONE",
            "host_write_authorization_sha256": None,
            "created_at": CREATED_AT,
            "expires_at": EXPIRES_AT,
        }

    register_canon_task_edge(
        root, project_id=SOURCE_PROJECT, edge=edge("edge-one-two", first, second)
    )
    with pytest.raises(EvidenceLaneError) as cycle:
        register_canon_task_edge(
            root, project_id=SOURCE_PROJECT, edge=edge("edge-two-one", second, first)
        )
    assert _error_code(cycle) == "CANON_TASK_GRAPH_CYCLE_BLOCKED"
    assert not (root / "canon" / "graph" / "edge-two-one.json").exists()


def test_task_graph_supports_real_fan_out_and_fan_in(tmp_path: Path) -> None:
    root = _root(tmp_path, SOURCE_PROJECT)
    nodes = {
        name: _endpoint(SOURCE_PROJECT, name) for name in ("a", "b", "c", "d")
    }

    def edge(edge_id: str, source: dict, destination: dict) -> dict:
        return {
            "edge_id": edge_id,
            "source": source,
            "destination": destination,
            "direction": "DOWNSTREAM",
            "contract_sha256": _hash(f"{edge_id}:contract"),
            "schema_sha256": _hash(f"{edge_id}:schema"),
            "edge_revision": 1,
            "permitted_actions": ["READ_EVIDENCE"],
            "dependency_ids": [],
            "expected_return_contract_sha256": _hash(f"{edge_id}:return"),
            "independent_hil_owner_task_uuid": destination["task_uuid"],
            "task_mode": "TOP_LEVEL_TASK",
            "scope_class": "READ_ONLY",
            "permitted_paths": [],
            "permitted_tools": ["repository_read"],
            "one_writer": True,
            "subagent_hil_allowed": False,
            "approval_propagation_allowed": False,
            "pointer_propagation_allowed": False,
            "source_write_authority_granted": False,
            "source_write_authority_source": "NONE",
            "host_write_authorization_sha256": None,
            "created_at": CREATED_AT,
            "expires_at": EXPIRES_AT,
        }

    for edge_id, source, destination in (
        ("edge-a-b", nodes["a"], nodes["b"]),
        ("edge-a-c", nodes["a"], nodes["c"]),
        ("edge-b-d", nodes["b"], nodes["d"]),
        ("edge-c-d", nodes["c"], nodes["d"]),
    ):
        register_canon_task_edge(
            root,
            project_id=SOURCE_PROJECT,
            edge=edge(edge_id, source, destination),
        )
    graph = inspect_canon_task_graph(root, project_id=SOURCE_PROJECT)

    assert len(graph["edges"]) == 4
    assert len(graph["nodes"]) == 4
    assert graph["fan_out_supported"] is True
    assert graph["fan_in_supported"] is True
    assert graph["cycles_allowed"] is False


def test_invalid_revision_leaves_no_destination_artifact(tmp_path: Path) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    invalid = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=_pointer(SOURCE_PROJECT),
        contract_sha256=_hash("undefined-revision"),
        revision=2,
        supersedes="canon_missing_predecessor",
        idempotency_key="canon-invalid-revision-002",
    )

    with pytest.raises(EvidenceLaneError) as missing:
        receive_canon_envelope(
            destination_root,
            project_id=DESTINATION_PROJECT,
            envelope=invalid,
            received_at=RECEIVED_AT,
        )
    assert _error_code(missing) == "CANON_PACKET_NOT_FOUND"
    assert not (
        destination_root / "canon" / "inbox" / f"{invalid['canon_id']}.json"
    ).exists()


def test_backfire_is_conditional_deduplicated_and_never_retries(tmp_path: Path) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    destination_root = _root(tmp_path, DESTINATION_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    pointer = _pointer(SOURCE_PROJECT)
    contract = _contract(
        destination_root,
        destination_project=DESTINATION_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
    )
    envelope = _seal(
        source_root,
        source_project=SOURCE_PROJECT,
        source=source,
        destination=destination,
        source_pointer=pointer,
        contract_sha256=contract["contract_sha256"],
    )
    receive_canon_envelope(
        destination_root,
        project_id=DESTINATION_PROJECT,
        envelope=envelope,
        received_at=RECEIVED_AT,
    )
    kwargs = {
        "project_id": DESTINATION_PROJECT,
        "admitted_canon_id": envelope["canon_id"],
        "failure_class": "MISSING_INFORMATION_FROM_SOURCE",
        "recipient": source,
        "requested_contract_id": "contract:backfire",
        "requested_contract_version": 1,
        "requested_contract_sha256": _hash("backfire-contract"),
        "requested_schema_id": "canon.backfire.v1",
        "requested_schema_version": "1",
        "requested_schema_sha256": _hash("backfire-schema"),
        "requested_revision": 2,
        "evidence_refs": [{"ref": "file:failure.json", "sha256": _hash("failure")}],
        "dependency_ids": [envelope["canon_id"]],
        "return_route": destination,
        "source_pointer": _pointer(DESTINATION_PROJECT, pv_ref="PV3", generation=3),
        "expected_return_contract_sha256": _hash("backfire-return"),
        "expires_at": EXPIRES_AT,
        "raised_at": DECIDED_AT,
    }
    first = raise_canon_backfire(destination_root, **kwargs)
    replay = raise_canon_backfire(destination_root, **kwargs)

    assert first["idempotent_reuse"] is False
    assert replay["idempotent_reuse"] is True
    assert first["envelope"]["payload"]["automatic_retry_allowed"] is False
    assert first["envelope"]["project_truth_pointer_moved"] is False

    changed = dict(kwargs)
    changed["evidence_refs"] = [
        {"ref": "file:other.json", "sha256": _hash("other")}
    ]
    with pytest.raises(EvidenceLaneError) as conflict:
        raise_canon_backfire(destination_root, **changed)
    assert _error_code(conflict) == "CANON_BACKFIRE_DEDUP_CONFLICT"


def test_canon_blocks_secrets_authority_escalation_and_state_travel_hil_carry(
    tmp_path: Path,
) -> None:
    source_root = _root(tmp_path, SOURCE_PROJECT)
    source = _endpoint(SOURCE_PROJECT, "source")
    destination = _endpoint(DESTINATION_PROJECT, "destination")
    base = {
        "source_project": SOURCE_PROJECT,
        "source": source,
        "destination": destination,
        "source_pointer": _pointer(SOURCE_PROJECT),
        "contract_sha256": _hash("undefined"),
    }
    with pytest.raises(EvidenceLaneError) as secret:
        _seal(source_root, payload={"api_key": "secret-value"}, **base)
    assert _error_code(secret) == "CANON_PAYLOAD_SECRET_BLOCKED"
    with pytest.raises(EvidenceLaneError) as escalation:
        _seal(source_root, permitted_actions=["FUSE"], **base)
    assert _error_code(escalation) == "CANON_AUTHORITY_ESCALATION_BLOCKED"

    pointer = _pointer(SOURCE_PROJECT)
    (source_root / "active_pointer.json").write_text(
        json.dumps(
            {
                "project_id": SOURCE_PROJECT,
                "accepted_pv": pointer["pv_ref"],
                "generation": pointer["generation"],
                "accepted_manifest_sha256": pointer["manifest_sha256"],
            }
        ),
        encoding="utf-8",
    )
    continuity = seal_canon_state_travel_continuity(
        source_root,
        project_id=SOURCE_PROJECT,
        source_task_uuid=source["task_uuid"],
        source_task_deep_link=source["task_deep_link"],
        handoff_id="travel-fixture",
        accepted_pv=pointer["pv_ref"],
        pointer_generation=pointer["generation"],
        created_at=CREATED_AT,
    )
    assert continuity["snapshot"]["decision_tokens_carried"] is False
    assert continuity["snapshot"]["canon_state_mutated"] is False
    assert continuity["snapshot"]["project_pointer_moved"] is False
    restored = restore_canon_state_travel_continuity(
        source_root,
        project_id=SOURCE_PROJECT,
        snapshot=continuity["snapshot"],
        destination_task_uuid="task-destination",
        destination_task_deep_link="codex://tasks/task-destination",
        destination_host_session_id="host-destination",
        restored_at=DECIDED_AT,
    )
    replay = restore_canon_state_travel_continuity(
        source_root,
        project_id=SOURCE_PROJECT,
        snapshot=continuity["snapshot"],
        destination_task_uuid="task-destination",
        destination_task_deep_link="codex://tasks/task-destination",
        destination_host_session_id="host-destination",
        restored_at=DECIDED_AT,
    )
    assert restored["idempotent_reuse"] is False
    assert replay["idempotent_reuse"] is True
    assert restored["receipt"]["canon_decision_replayed"] is False
    assert restored["receipt"]["canon_packet_state_changed"] is False
