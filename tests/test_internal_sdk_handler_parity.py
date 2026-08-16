from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.internal_sdk import (
    SDK_EXTERNAL_PROVIDER_OPERATIONS,
    SDK_NARROWED_OPERATION_CLAIMS,
    InternalEvidenceLaneSDK,
    SDKBinding,
    SDKCancellationToken,
    SDKInvocationContext,
    build_local_service_adapter,
    inspect_sdk_handler_parity,
)


def _hash(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _binding() -> SDKBinding:
    return SDKBinding.from_dict(
        {
            "project_id": "sdk-handler-parity",
            "session_id": "session-sdk-handler-parity",
            "task_id": "EL-CODEX-SDK-HANDLER-PARITY",
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "accepted_manifest_sha256": _hash("manifest"),
            "lineage_head_sha256": _hash("lineage"),
            "env_authority_sha256": _hash("env"),
            "uop_authority_sha256": _hash("uop"),
            "derived_projection_sha256": _hash("projection"),
            "flash_receipt_sha256": _hash("flash"),
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "ultra",
            "reasoning_speed": "standard",
            "host_kind": "CODEX_DESKTOP",
            "host_session_id": "host-session-sdk-handler-parity",
            "write_scope": [
                "chat_lineage:append",
                "env_uop_operator_runtime:classify_mode",
                "plan_delta_tasks:transition_task",
                "source_lane_retrieval:source_intake",
                "storage_connectors:select",
                "hil_candidate_pointer:record_decision",
                "hil_candidate_pointer:fuse",
                "hil_candidate_pointer:rollback",
            ],
        }
    )


class _Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def project_root(self, project_id: str) -> Path:
        result = self.root / project_id
        result.mkdir(parents=True, exist_ok=True)
        return result


class _Service:
    def __init__(self, root: Path) -> None:
        self.store = _Store(root)
        self.calls: list[tuple[str, str, str, dict[str, Any]]] = []

    def _record(
        self,
        operation: str,
        project_id: str,
        session_id: str = "",
        **payload: Any,
    ) -> dict[str, Any]:
        self.calls.append((operation, project_id, session_id, payload))
        return {"status": "PASS", "operation": operation}

    def transition_task(self, project_id: str, **payload: Any) -> dict[str, Any]:
        return self._record("transition_task", project_id, **payload)

    def task_backlog_window(
        self, project_id: str, **payload: Any
    ) -> dict[str, Any]:
        return {
            **self._record("task_backlog_window", project_id, **payload),
            "rows": [],
            "full_ledger_returned": False,
            "accepted_pv_payload_loaded": False,
        }

    def classify_mode(
        self, project_id: str, request: str, **payload: Any
    ) -> dict[str, Any]:
        return {
            **self._record("classify_mode", project_id, **payload),
            "request": request,
            "chat_lineage": {"append_status": "APPENDED"},
        }

    def source_intake(
        self, project_id: str, *, session_id: str, **payload: Any
    ) -> dict[str, Any]:
        return self._record("source_intake", project_id, session_id, **payload)

    def storage_connector_select(
        self, project_id: str, **payload: Any
    ) -> dict[str, Any]:
        return self._record("storage_select", project_id, **payload)

    def record_hil_decision(
        self, project_id: str, session_id: str, **payload: Any
    ) -> dict[str, Any]:
        return self._record("record_decision", project_id, session_id, **payload)

    def fuse(
        self, project_id: str, session_id: str, **payload: Any
    ) -> dict[str, Any]:
        return self._record("fuse", project_id, session_id, **payload)

    def rollback(
        self, project_id: str, session_id: str, **payload: Any
    ) -> dict[str, Any]:
        return self._record("rollback", project_id, session_id, **payload)


def _context(request_id: str) -> SDKInvocationContext:
    return SDKInvocationContext(
        request_id=request_id,
        timeout_ms=5_000,
        started_monotonic=time.monotonic(),
        cancellation=SDKCancellationToken(),
    )


def test_production_local_adapter_classifies_every_declared_sdk_operation(
    tmp_path: Path,
) -> None:
    adapter = build_local_service_adapter(
        _Service(tmp_path), runtime_binding=_binding().as_dict()
    )
    parity = inspect_sdk_handler_parity(
        adapter, construction_profile="LOCAL_SERVICE"
    )

    assert parity["status"] == "PASS"
    assert parity["declared_operation_count"] == 60
    assert parity["registered_local_handler_count"] == 53
    assert parity["external_provider_operation_count"] == 7
    assert parity["narrowed_operation_claim_count"] == 5
    assert parity["unclassified_operation_count"] == 0
    body = {key: value for key, value in parity.items() if key != "receipt_sha256"}
    assert parity["receipt_sha256"] == sha256_bytes(canonical_json_bytes(body))

    sdk = InternalEvidenceLaneSDK(
        tmp_path / _binding().project_id,
        adapter,
    )
    status = sdk.capability_status()
    assert all(not module["unclassified_operations"] for module in status["modules"])
    external = {
        (module["module_id"], operation): provider
        for module in status["modules"]
        for operation, provider in module["external_provider_operations"].items()
    }
    assert external == SDK_EXTERNAL_PROVIDER_OPERATIONS


def test_new_local_handlers_bind_exact_project_session_task_and_effects(
    tmp_path: Path,
) -> None:
    service = _Service(tmp_path)
    binding = _binding()
    adapter = build_local_service_adapter(
        service, runtime_binding=binding.as_dict()
    )

    lineage = adapter.invoke(
        "chat_lineage",
        "append",
        binding,
        {
            "event_type": "user.prompt",
            "visible_payload": {"text": "bounded visible prompt"},
            "occurred_at": "2026-08-15T17:00:00Z",
        },
        _context("lineage-append"),
    )
    assert lineage["authority_effects"]["chat_lineage"] == "APPENDED"

    adapter.invoke(
        "plan_delta_tasks",
        "transition_task",
        binding,
        {
            "task_id": binding.task_id,
            "transition_name": "DROP",
            "decided_by": "sdk-test",
            "reason": "Exact unit handler proof.",
        },
        _context("plan-transition"),
    )
    adapter.invoke(
        "source_lane_retrieval",
        "source_intake",
        binding,
        {"sources": ["README.md"], "authority_mode": "CLASSIFICATION_ONLY"},
        _context("source-intake"),
    )
    adapter.invoke(
        "storage_connectors",
        "select",
        binding,
        {"connector_id": "local-sqlite"},
        _context("storage-select"),
    )
    adapter.invoke(
        "hil_candidate_pointer",
        "record_decision",
        binding,
        {"decision": "MORE_RESEARCH", "decision_id": "fake-unit-decision"},
        _context("hil-record"),
    )
    fused = adapter.invoke(
        "hil_candidate_pointer",
        "fuse",
        binding,
        {"approval": "APPROVE", "decided_by": "sdk-test"},
        _context("hil-fuse"),
    )
    rolled_back = adapter.invoke(
        "hil_candidate_pointer",
        "rollback",
        binding,
        {"rollback_to": "PV11", "decided_by": "sdk-test"},
        _context("hil-rollback"),
    )

    assert fused["authority_effects"]["project_truth"] == "FUSED"
    assert rolled_back["authority_effects"]["project_truth"] == "ROLLED_BACK"
    assert [call[0] for call in service.calls] == [
        "transition_task",
        "source_intake",
        "storage_select",
        "record_decision",
        "fuse",
        "rollback",
    ]
    assert all(call[1] == binding.project_id for call in service.calls)
    session_bound = {call[0]: call[2] for call in service.calls}
    assert session_bound["source_intake"] == binding.session_id
    assert session_bound["record_decision"] == binding.session_id
    assert session_bound["fuse"] == binding.session_id
    assert session_bound["rollback"] == binding.session_id
    lineage_path = (
        tmp_path
        / binding.project_id
        / "lineage"
        / f"{binding.session_id}.jsonl"
    )
    assert lineage_path.is_file()


def test_mode_and_plan_handlers_preserve_binding_and_bounded_projection(
    tmp_path: Path,
) -> None:
    service = _Service(tmp_path)
    binding = _binding()
    adapter = build_local_service_adapter(
        service, runtime_binding=binding.as_dict()
    )

    mode = adapter.invoke(
        "env_uop_operator_runtime",
        "classify_mode",
        binding,
        {"request": "inspect current mode", "explicit_modes": ["CODE"]},
        _context("mode-classify"),
    )
    plan = adapter.invoke(
        "plan_delta_tasks",
        "backlog",
        binding,
        {"limit": 10},
        _context("plan-backlog"),
    )
    capabilities = adapter.invoke(
        "provider_host_adapters",
        "capabilities",
        binding,
        {},
        _context("provider-capabilities"),
    )

    mode_call = next(call for call in service.calls if call[0] == "classify_mode")
    assert mode_call == (
        "classify_mode",
        binding.project_id,
        binding.session_id,
        {"explicit_modes": ["CODE"]},
    )
    assert mode["request"] == "inspect current mode"
    assert mode["authority_effects"]["chat_lineage"] == "APPENDED"
    assert plan["full_ledger_returned"] is False
    assert plan["accepted_pv_payload_loaded"] is False
    assert ("task_backlog_window", binding.project_id, "", {"limit": 10}) in (
        service.calls
    )
    assert capabilities["headless_supported"] is False
    assert capabilities["headless_provider_adapter_id"] == (
        "codex-headless-provider.v1"
    )


def test_parity_rejects_missing_local_handler_or_external_impersonation(
    tmp_path: Path,
) -> None:
    adapter = build_local_service_adapter(
        _Service(tmp_path), runtime_binding=_binding().as_dict()
    )
    adapter._handlers.pop(("chat_lineage", "append"))
    with pytest.raises(EvidenceLaneError) as missing:
        inspect_sdk_handler_parity(adapter, construction_profile="LOCAL_SERVICE")
    assert missing.value.code == "SDK_HANDLER_PARITY_MISMATCH"

    adapter = build_local_service_adapter(
        _Service(tmp_path), runtime_binding=_binding().as_dict()
    )
    adapter._handlers[("env_uop_operator_runtime", "compile_formula")] = (
        lambda binding, payload, context: {"status": "PASS"}
    )
    with pytest.raises(EvidenceLaneError) as impersonation:
        inspect_sdk_handler_parity(adapter, construction_profile="LOCAL_SERVICE")
    assert impersonation.value.code == "SDK_EXTERNAL_PROVIDER_IMPERSONATION_BLOCKED"


def test_narrowed_claims_are_absent_from_abi_and_keep_exact_reasons() -> None:
    catalog = InternalEvidenceLaneSDK.module_catalog()
    declared = {
        f"{module['module_id']}:{operation['name']}"
        for module in catalog["modules"]
        for operation in module["operations"]
    }

    assert set(SDK_NARROWED_OPERATION_CLAIMS).isdisjoint(declared)
    assert catalog["narrowed_operation_claims"] == SDK_NARROWED_OPERATION_CLAIMS
    assert all(reason.strip() for reason in SDK_NARROWED_OPERATION_CLAIMS.values())
