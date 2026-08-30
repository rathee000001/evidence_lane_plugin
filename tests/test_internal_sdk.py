from __future__ import annotations

import time
from pathlib import Path

import pytest
from evidence_lane_plugin.constants import NATIVE_TOOL_COUNT
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_bytes
from evidence_lane_plugin.internal_sdk import (
    INTERNAL_SDK_ABI,
    InternalEvidenceLaneSDK,
    RegisteredSDKAdapter,
    SDKBinding,
    SDKCancellationToken,
    build_local_service_adapter,
    internal_support_binding_registry,
    runtime_workflow_sdk_registry,
    whole_plugin_sdk_governance_registry,
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
        "project_memory": "NONE",
        "project_universe": "NONE",
        "chat_lineage": "NONE",
        "host_entry_continuity": "NONE",
    }
    result.update(changes)
    return result


def test_sdk_binding_accepts_model_only_without_inventing_optional_selectors() -> None:
    values = _binding().as_dict()
    for field in ("submodel", "reasoning_effort", "reasoning_speed"):
        values.pop(field)

    binding = SDKBinding.from_dict(values)

    assert binding.model == "gpt-5.6-sol"
    assert binding.submodel is None
    assert binding.reasoning_effort is None
    assert binding.reasoning_speed is None
    assert set(binding.as_dict()).isdisjoint(
        {"submodel", "reasoning_effort", "reasoning_speed"}
    )


@pytest.mark.parametrize("field", ["submodel", "reasoning_effort", "reasoning_speed"])
def test_sdk_binding_rejects_blank_supplied_optional_selector(field: str) -> None:
    values = _binding().as_dict()
    values[field] = ""

    with pytest.raises(EvidenceLaneError):
        SDKBinding.from_dict(values)


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
        "project_memory",
        "project_universe",
        "chat_lineage",
        "host_entry_continuity",
        "lifecycle_hooks",
        "plan_delta_tasks",
        "source_lane_retrieval",
        "env_uop_operator_runtime",
        "storage_connectors",
        "hil_candidate_pointer",
        "provider_host_adapters",
        "first_class_workflows",
    }
    assert len({row["namespace"] for row in modules.values()}) == len(modules)
    assert all(row["independent_replay_ledger"] for row in modules.values())
    universe = modules["project_universe"]
    assert {row["name"] for row in universe["operations"]} == {
        "status",
        "query",
        "refresh",
    }


def test_internal_support_modules_are_bound_to_exact_sdk_owners() -> None:
    receipt = internal_support_binding_registry()
    rows = {row["component"]: row for row in receipt["components"]}

    assert receipt["status"] == "PASS"
    assert set(rows) == {
        "env_uop_graph",
        "env_uop_tool_routing",
        "evaluation_toolchain",
        "github_toolchain",
        "live_root_normalization",
        "runtime_api",
    }
    assert rows["env_uop_graph"]["owner_module"] == "env_uop_operator_runtime"
    assert rows["env_uop_tool_routing"]["owner_module"] == ("env_uop_operator_runtime")
    assert rows["evaluation_toolchain"]["owner_module"] == "first_class_workflows"
    assert rows["github_toolchain"]["owner_module"] == "first_class_workflows"
    assert rows["live_root_normalization"]["owner_module"] == "storage_connectors"
    assert rows["runtime_api"]["owner_module"] == "provider_host_adapters"
    assert receipt["support_components_inflate_public_action_count"] is False


def test_whole_plugin_behavior_is_internal_sdk_governed() -> None:
    receipt = whole_plugin_sdk_governance_registry()

    assert receipt["status"] == "PASS"
    assert receipt["law"] == "ALL_PLUGIN_BEHAVIOR_INTERNAL_SDK_GOVERNED"
    assert receipt["public_action_count"] == NATIVE_TOOL_COUNT
    assert receipt["missing_adapter_capabilities"] == []
    assert receipt["missing_sdk_governance"] == []
    assert receipt["outer_logic_violations"] == []
    assert receipt["helper_tunnel_hooks_are_sdk_governed_thin_adapters"] is True
    assert receipt["helper_or_tunnel_lifecycle_reasoning_allowed"] is False
    assert receipt["counts_are_derived_not_fixed"] is True


def test_runtime_workflows_bind_prompt_delta_relock_and_hooks_to_sdk() -> None:
    receipt = runtime_workflow_sdk_registry()
    by_name = {row["workflow"]: row for row in receipt["workflows"]}

    assert receipt["status"] == "PASS"
    assert receipt["internal_sdk_public_action_count"] == NATIVE_TOOL_COUNT
    assert receipt["missing_public_actions"] == []
    assert receipt["missing_sdk_modules"] == []
    assert receipt["all_explicit_actions_work_with_hooks_off"] is True
    assert receipt["sdk_claims_pre_reasoning_prompt_interception"] is False
    assert by_name["PROMPT_OR_STEER_ENTRY"]["hooks_required"] is False
    assert (
        by_name["DELTA_ENTRY_AND_CURRENT_AUTHORITY_QUERY"][
            "accepted_archive_query_allowed"
        ]
        is False
    )
    assert (
        by_name["DELTA_EXIT_APPEND_REFRESH"]["ordinary_project_overlay_allowed"]
        is False
    )
    assert (
        by_name["STEP_TASK_LIST_RELOCK"]["identical_fingerprint_reattachment_required"]
        is True
    )
    assert (
        by_name["STEP_TASK_LIST_RELOCK"][
            "plan_mutation_allowed_for_panel_drop_or_restart"
        ]
        is False
    )
    assert by_name["HOOK_EVENT_TRANSPORT"]["logical_sub_actions"] == 44


def test_canon_sdk_arm_exposes_full_engine_with_fail_closed_host_dispatch() -> None:
    catalog = InternalEvidenceLaneSDK.module_catalog()
    canon = next(row for row in catalog["modules"] if row["module_id"] == "canon_input")
    contract_operations = {row["name"] for row in canon["operations"]}
    assert contract_operations == {
        "inspect",
        "inbox",
        "graph",
        "bootstrap_consequence_graph",
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
    assert contract_operations == available


def test_learning_sdk_arm_exposes_candidate_lifecycle() -> None:
    catalog = InternalEvidenceLaneSDK.module_catalog()
    learning = next(
        row for row in catalog["modules"] if row["module_id"] == "agent_learning"
    )
    contract_operations = {row["name"] for row in learning["operations"]}
    assert contract_operations == {
        "inspect",
        "retrieve",
        "bootstrap_verified_history",
        "record_host_memory_import",
        "seal_candidate",
        "decide_candidate",
        "revoke",
    }

    adapter = build_local_service_adapter(
        object(), runtime_binding=_binding().as_dict()
    )
    assert adapter.available_operations()["agent_learning"] == contract_operations


def test_memory_sdk_arm_is_independent_from_learning_and_project_truth() -> None:
    catalog = InternalEvidenceLaneSDK.module_catalog()
    memory = next(
        row for row in catalog["modules"] if row["module_id"] == "project_memory"
    )
    contract_operations = {row["name"] for row in memory["operations"]}
    assert memory["authority"] == "PROJECT_MEMORY"
    assert contract_operations == {
        "inspect",
        "bootstrap",
        "query",
        "record_link",
        "seal_checkpoint",
        "rehydrate_checkpoint",
    }

    adapter = build_local_service_adapter(
        object(), runtime_binding=_binding().as_dict()
    )
    assert adapter.available_operations()["project_memory"] == contract_operations


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
    ledgers = sorted((sdk.project_root / "receipts" / "sdk-replay").glob("*.sqlite"))
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


def test_sdk_public_response_withholds_authority_blob_and_replays_receipt(
    tmp_path: Path,
) -> None:
    binding = _binding()
    calls = {"count": 0}

    def status(bound, payload, context):
        calls["count"] += 1
        return {
            "status": "PASS",
            "accepted_pv": bound.accepted_pv,
            "raw_markdown": "private SDK authority\n" * 5_000,
        }

    root = _root(tmp_path)
    adapter = _adapter(binding, {("project_truth", "status"): status})
    first = InternalEvidenceLaneSDK(root, adapter).invoke(
        module_id="project_truth",
        operation="status",
        binding=binding,
        payload={},
        request_id="sdk-bounded-output-001",
    )
    replay = InternalEvidenceLaneSDK(root, adapter).invoke(
        module_id="project_truth",
        operation="status",
        binding=binding,
        payload={},
        request_id="sdk-bounded-output-001",
    )

    assert first["data"]["schema"] == ("evidence-lane.internal-sdk-withheld-receipt.v1")
    assert first["data"]["payload_withheld"] is True
    assert first["data"]["raw_payload_returned"] is False
    assert first["public_result_boundary"]["full_replay_retained_in_sqlite"] is True
    assert "private SDK authority" not in str(first)
    assert replay["replay"] == "IDEMPOTENT_REUSE"
    assert replay["data"]["payload_withheld"] is True
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


def test_universe_sdk_refresh_is_independent_and_write_scoped(tmp_path: Path) -> None:
    ungranted = _binding()

    def refresh(bound, payload, context):
        return {
            "status": "PASS",
            "graph_sha256": _hash("universe"),
            "authority_effects": _effects(project_universe="REFRESHED"),
        }

    sdk = InternalEvidenceLaneSDK(
        _root(tmp_path),
        _adapter(ungranted, {("project_universe", "refresh"): refresh}),
    )
    with pytest.raises(EvidenceLaneError) as blocked:
        sdk.invoke(
            module_id="project_universe",
            operation="refresh",
            binding=ungranted,
            payload={},
            request_id="sdk-universe-001",
        )
    assert blocked.value.code == "SDK_WRITE_SCOPE_REQUIRED"

    granted = _binding(write_scope=("project_universe:refresh",))
    result = sdk.invoke(
        module_id="project_universe",
        operation="refresh",
        binding=granted,
        payload={},
        request_id="sdk-universe-002",
    )
    assert result["status"] == "PASS"
    assert result["authority_effects"]["project_universe"] == "REFRESHED"
    assert result["authority_effects"]["project_truth"] == "NONE"


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
    ledger = (
        sdk.project_root / "receipts" / "sdk-replay" / "sdk.project-truth.v1.sqlite"
    )
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
    assert (first.project_root / "receipts" / "sdk-replay").is_dir()
    assert (second.project_root / "receipts" / "sdk-replay").is_dir()
