from __future__ import annotations

import time
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_bytes
from evidence_lane_plugin.internal_sdk import (
    INTERNAL_SDK_ABI,
    InternalEvidenceLaneSDK,
    RegisteredSDKAdapter,
    SDKBinding,
    SDKCancellationToken,
    build_local_service_adapter,
)

PROJECT_ID = "sdk-fixture"


def _hash(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _binding(
    *,
    project_id: str = PROJECT_ID,
    model: str = "gpt-5.6-sol",
    pointer_generation: int = 12,
    write_scope: tuple[str, ...] = (),
) -> SDKBinding:
    return SDKBinding.from_dict(
        {
            "project_id": project_id,
            "session_id": "session-sdk-fixture",
            "task_id": "EL-CODEX-INTERNAL-SDK-HEADLESS-ADAPTER-PROPOSAL-26",
            "accepted_pv": "PV12",
            "pointer_generation": pointer_generation,
            "accepted_manifest_sha256": _hash("manifest"),
            "lineage_head_sha256": _hash("lineage"),
            "env_authority_sha256": _hash("env"),
            "uop_authority_sha256": _hash("uop"),
            "derived_projection_sha256": _hash("projection"),
            "flash_receipt_sha256": _hash("flash"),
            "model": model,
            "submodel": "sol",
            "reasoning_effort": "ultra",
            "reasoning_speed": "standard",
            "host_kind": "CODEX_DESKTOP",
            "host_session_id": "host-session-sdk-fixture",
            "write_scope": list(write_scope),
        }
    )


def _effects(**changes: str) -> dict[str, str]:
    result = {
        "project_truth": "NONE",
        "canon_input": "NONE",
        "agent_learning": "NONE",
        "chat_lineage": "NONE",
        "host_entry_continuity": "NONE",
    }
    result.update(changes)
    return result


def _root(tmp_path: Path, project_id: str = PROJECT_ID) -> Path:
    root = tmp_path / project_id
    root.mkdir()
    return root


def _adapter(
    binding: SDKBinding,
    handlers: dict,
    *,
    snapshot=None,
) -> RegisteredSDKAdapter:
    return RegisteredSDKAdapter(
        adapter_id="fixture.provider.v1",
        snapshot_provider=snapshot or (lambda supplied: supplied.as_dict()),
        handlers=handlers,
    )


def test_sdk_catalog_covers_every_independent_governed_arm() -> None:
    catalog = InternalEvidenceLaneSDK.module_catalog()

    assert catalog["abi"] == INTERNAL_SDK_ABI
    assert catalog["distribution"] == "PRIVATE_INTERNAL_CODEX_LAYER"
    assert catalog["top_level_role"] == "ROUTE_DISCOVER_VALIDATE_ONLY"
    assert catalog["authority_merge_allowed"] is False
    modules = {row["module_id"]: row for row in catalog["modules"]}
    assert set(modules) == {
        "project_truth",
        "canon_input",
        "agent_learning",
        "chat_lineage",
        "host_entry_continuity",
        "lifecycle_hooks",
        "plan_delta_tasks",
        "source_lane_retrieval",
        "env_uop_operator_runtime",
        "storage_connectors",
        "hil_candidate_pointer",
        "provider_host_adapters",
    }
    assert len({row["namespace"] for row in modules.values()}) == len(modules)
    assert all(row["independent_replay_ledger"] for row in modules.values())


def test_canon_sdk_arm_exposes_full_engine_and_truthful_host_gap() -> None:
    catalog = InternalEvidenceLaneSDK.module_catalog()
    canon = next(row for row in catalog["modules"] if row["module_id"] == "canon_input")
    contract_operations = {row["name"] for row in canon["operations"]}
    assert contract_operations == {
        "inspect",
        "inbox",
        "graph",
        "register_contract",
        "seal_envelope",
        "receive",
        "classify",
        "decide",
        "supersede",
        "register_edge",
        "bind_edge",
        "dispatch_linked_task",
        "backfire_hil",
        "seal_result",
        "seal_continuity",
        "restore_continuity",
    }

    adapter = build_local_service_adapter(
        object(), runtime_binding=_binding().as_dict()
    )
    available = adapter.available_operations()["canon_input"]
    assert contract_operations - {"dispatch_linked_task"} == available
    assert "dispatch_linked_task" not in available


def test_separate_retrieval_never_fuses_truth_and_learning(tmp_path: Path) -> None:
    binding = _binding()

    def truth(bound, payload, context):
        context.checkpoint()
        return {
            "status": "PASS",
            "result": "HIT",
            "results": [{"authority": "PROJECT_TRUTH", "ref": "truth://one"}],
        }

    def learning(bound, payload, context):
        context.checkpoint()
        return {
            "status": "PASS",
            "result": "HIT",
            "hits": [{"authority": "AGENT_LEARNING", "ref": "learn://one"}],
            "project_truth_slice": None,
        }

    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(
            binding,
            {
                ("project_truth", "search"): truth,
                ("agent_learning", "retrieve"): learning,
            },
        ),
    )

    result = sdk.retrieve_separately(
        binding=binding,
        project_truth_payload={"query": "bounded truth", "limit": 3},
        learning_payload={"query": "bounded learning", "limit": 3},
        request_prefix="sdk-separate-001",
    )

    assert result["status"] == "PASS"
    assert result["combined_ranking"] is False
    assert result["authority_merge_allowed"] is False
    assert result["canon_input_slice"] is None
    assert result["host_entry_slice"] is None
    assert result["project_truth_slice"]["authority"] == "PROJECT_TRUTH"
    assert result["agent_learning_slice"]["authority"] == "AGENT_LEARNING"
    ledgers = sorted((sdk.project_root / "sdk" / "replay").glob("*.sqlite"))
    assert [path.name for path in ledgers] == [
        "sdk.agent-learning.v1.sqlite",
        "sdk.project-truth.v1.sqlite",
    ]


def test_stateless_restart_and_cache_loss_replay_from_sqlite(tmp_path: Path) -> None:
    binding = _binding()
    calls = {"count": 0}

    def status(bound, payload, context):
        calls["count"] += 1
        return {"status": "PASS", "accepted_pv": bound.accepted_pv}

    adapter = _adapter(binding, {("project_truth", "status"): status})
    root = _root(tmp_path)
    first_sdk = InternalEvidenceLaneSDK(root, adapter)
    first = first_sdk.invoke(
        module_id="project_truth",
        operation="status",
        binding=binding,
        payload={},
        request_id="sdk-restart-001",
    )

    del first_sdk
    restarted_sdk = InternalEvidenceLaneSDK(root, adapter)
    replay = restarted_sdk.invoke(
        module_id="project_truth",
        operation="status",
        binding=binding,
        payload={},
        request_id="sdk-restart-001",
    )

    assert first["replay"] == "RECORDED"
    assert replay["replay"] == "IDEMPOTENT_REUSE"
    assert replay["receipt_sha256"] == first["receipt_sha256"]
    assert calls["count"] == 1


def test_model_change_requires_a_fresh_request_identity(tmp_path: Path) -> None:
    first_binding = _binding()
    changed_binding = _binding(model="gpt-5.7-sol")
    handler = lambda bound, payload, context: {"status": "PASS"}
    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(first_binding, {("project_truth", "status"): handler}),
    )
    sdk.invoke(
        module_id="project_truth",
        operation="status",
        binding=first_binding,
        payload={},
        request_id="sdk-model-001",
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        sdk.invoke(
            module_id="project_truth",
            operation="status",
            binding=changed_binding,
            payload={},
            request_id="sdk-model-001",
        )

    assert blocked.value.code == "SDK_REPLAY_CONFLICT"
    fresh = sdk.invoke(
        module_id="project_truth",
        operation="status",
        binding=changed_binding,
        payload={},
        request_id="sdk-model-002",
    )
    assert fresh["status"] == "PASS"


def test_live_pointer_or_task_mismatch_fails_before_adapter_call(
    tmp_path: Path,
) -> None:
    binding = _binding()
    changed = _binding(pointer_generation=13)
    calls = {"count": 0}

    def status(bound, payload, context):
        calls["count"] += 1
        return {"status": "PASS"}

    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(
            binding,
            {("project_truth", "status"): status},
            snapshot=lambda supplied: binding.as_dict(),
        ),
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        sdk.invoke(
            module_id="project_truth",
            operation="status",
            binding=changed,
            payload={},
            request_id="sdk-pointer-001",
        )

    assert blocked.value.code == "SDK_BINDING_MISMATCH"
    assert "pointer_generation" in blocked.value.details["mismatches"]
    assert calls["count"] == 0


def test_no_hit_is_a_successful_explicit_result(tmp_path: Path) -> None:
    binding = _binding()
    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(
            binding,
            {
                ("agent_learning", "retrieve"): lambda bound, payload, context: {
                    "status": "PASS",
                    "result": "NO_HIT",
                    "hits": [],
                    "project_truth_slice": None,
                }
            },
        ),
    )

    result = sdk.invoke(
        module_id="agent_learning",
        operation="retrieve",
        binding=binding,
        payload={"query": "absent lesson", "limit": 4},
        request_id="sdk-no-hit-001",
    )

    assert result["status"] == "PASS"
    assert result["data"]["result"] == "NO_HIT"
    assert result["data"]["hits"] == []
    assert result["data"]["project_truth_slice"] is None


def test_unsupported_provider_operation_is_explicit(tmp_path: Path) -> None:
    binding = _binding(write_scope=("canon_input:receive",))
    sdk = InternalEvidenceLaneSDK(_root(tmp_path), _adapter(binding, {}))

    with pytest.raises(EvidenceLaneError) as unavailable:
        sdk.invoke(
            module_id="canon_input",
            operation="receive",
            binding=binding,
            payload={"envelope_id": "canon-fixture"},
            request_id="sdk-unavailable-001",
        )

    assert unavailable.value.code == "HOST_CAPABILITY_UNAVAILABLE"
    assert unavailable.value.status == "UNAVAILABLE"


def test_write_scope_and_cross_authority_promotion_are_fail_closed(
    tmp_path: Path,
) -> None:
    without_grant = _binding()

    def canon(bound, payload, context):
        return {
            "status": "PASS",
            "authority_effects": _effects(
                canon_input="ACCEPTED",
                project_truth="PROMOTED",
            ),
        }

    adapter = _adapter(
        without_grant,
        {("canon_input", "receive"): canon},
    )
    sdk = InternalEvidenceLaneSDK(_root(tmp_path), adapter)

    with pytest.raises(EvidenceLaneError) as scope_blocked:
        sdk.invoke(
            module_id="canon_input",
            operation="receive",
            binding=without_grant,
            payload={"envelope_id": "canon-fixture"},
            request_id="sdk-scope-001",
        )
    assert scope_blocked.value.code == "SDK_WRITE_SCOPE_REQUIRED"

    with_grant = _binding(write_scope=("canon_input:receive",))
    with pytest.raises(EvidenceLaneError) as authority_blocked:
        sdk.invoke(
            module_id="canon_input",
            operation="receive",
            binding=with_grant,
            payload={"envelope_id": "canon-fixture"},
            request_id="sdk-scope-002",
        )
    assert authority_blocked.value.code == "SDK_CROSS_AUTHORITY_EFFECT_BLOCKED"


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "plain-value"},
        {"header": "authorization: bearer-not-allowed"},
        {"nested": [{"password": "plain-value"}]},
    ],
)
def test_secret_material_never_enters_replay(payload: dict, tmp_path: Path) -> None:
    binding = _binding()
    calls = {"count": 0}

    def status(bound, supplied, context):
        calls["count"] += 1
        return {"status": "PASS"}

    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(binding, {("project_truth", "status"): status}),
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        sdk.invoke(
            module_id="project_truth",
            operation="status",
            binding=binding,
            payload=payload,
            request_id="sdk-secret-001",
        )

    assert blocked.value.code == "SDK_SECRET_MATERIAL_FORBIDDEN"
    assert calls["count"] == 0
    assert not (sdk.project_root / "sdk").exists()


def test_secret_adapter_result_is_not_persisted(tmp_path: Path) -> None:
    binding = _binding()
    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(
            binding,
            {
                ("project_truth", "status"): lambda bound, payload, context: {
                    "status": "PASS",
                    "access_token": "plain-value",
                }
            },
        ),
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        sdk.invoke(
            module_id="project_truth",
            operation="status",
            binding=binding,
            payload={},
            request_id="sdk-secret-result-001",
        )

    assert blocked.value.code == "SDK_ADAPTER_SECRET_RESULT_BLOCKED"


def test_cancellation_and_timeout_do_not_create_replay_receipts(tmp_path: Path) -> None:
    binding = _binding()

    def cooperative_slow(bound, payload, context):
        while not context.cancellation.cancelled:
            time.sleep(0.002)
        context.checkpoint()
        raise AssertionError("unreachable")

    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(
            binding,
            {("project_truth", "status"): cooperative_slow},
        ),
    )
    already_cancelled = SDKCancellationToken()
    already_cancelled.cancel()

    with pytest.raises(EvidenceLaneError) as cancelled:
        sdk.invoke(
            module_id="project_truth",
            operation="status",
            binding=binding,
            payload={},
            request_id="sdk-cancelled-001",
            cancellation=already_cancelled,
        )
    assert cancelled.value.code == "SDK_INVOCATION_CANCELLED"

    with pytest.raises(EvidenceLaneError) as timed_out:
        sdk.invoke(
            module_id="project_truth",
            operation="status",
            binding=binding,
            payload={},
            request_id="sdk-timeout-001",
            timeout_ms=20,
        )
    assert timed_out.value.code == "SDK_INVOCATION_TIMEOUT"
    time.sleep(0.03)
    ledger = sdk.project_root / "sdk" / "replay" / "sdk.project-truth.v1.sqlite"
    assert ledger.is_file()
    import sqlite3

    with sqlite3.connect(ledger) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sdk_replay").fetchone()[0] == 0


def test_parallel_projects_cannot_collide_replay_namespaces(tmp_path: Path) -> None:
    first_binding = _binding(project_id="sdk-project-a")
    second_binding = _binding(project_id="sdk-project-b")

    def status(bound, payload, context):
        return {"status": "PASS", "project_id": bound.project_id}

    first = InternalEvidenceLaneSDK(
        _root(tmp_path, "sdk-project-a"),
        _adapter(first_binding, {("project_truth", "status"): status}),
    )
    second = InternalEvidenceLaneSDK(
        _root(tmp_path, "sdk-project-b"),
        _adapter(second_binding, {("project_truth", "status"): status}),
    )

    left = first.invoke(
        module_id="project_truth",
        operation="status",
        binding=first_binding,
        payload={},
        request_id="sdk-parallel-001",
    )
    right = second.invoke(
        module_id="project_truth",
        operation="status",
        binding=second_binding,
        payload={},
        request_id="sdk-parallel-001",
    )

    assert left["data"]["project_id"] == "sdk-project-a"
    assert right["data"]["project_id"] == "sdk-project-b"
    assert left["binding_sha256"] != right["binding_sha256"]
    assert (first.project_root / "sdk" / "replay").is_dir()
    assert (second.project_root / "sdk" / "replay").is_dir()
