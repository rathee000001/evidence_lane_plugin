from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_bytes
from evidence_lane_plugin.internal_sdk import (
    InternalEvidenceLaneSDK,
    RegisteredSDKAdapter,
    SDKBinding,
    build_env_uop_operator_provider_adapter,
)
from evidence_lane_plugin.mode_governance import (
    ENV15_ENV_SQLITE_SHA256,
    ENV15_MODE_POLICY_PROJECTION_SHA256,
    ENV15_UOP_SQLITE_SHA256,
    ENV_UOP_COMPILED_FORMULA_SCHEMA,
    ENV_UOP_EXECUTION_BUDGET_SCHEMA,
    ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
    ENV_UOP_OPERATOR_ROUTE_RECEIPT_SCHEMA,
    bind_env_uop_operator_effect,
    compile_env_uop_formula,
    env_uop_authority_boundary,
    route_env_uop_operator,
    validate_env_uop_external_secret_reference,
    validate_mode_governance_selection,
)
from evidence_lane_plugin.operating_modes import classify_operating_modes

from .conftest import boot_local


def _env_uop_hash(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _env_uop_binding() -> SDKBinding:
    return SDKBinding.from_dict(
        {
            "project_id": "env-uop-fixture",
            "session_id": "session-env-uop-fixture",
            "task_id": "EL-CODEX-T6-PARITY-029-ENV-UOP-EXECUTION",
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "accepted_manifest_sha256": _env_uop_hash("manifest"),
            "lineage_head_sha256": _env_uop_hash("lineage"),
            "env_authority_sha256": _env_uop_hash("env"),
            "uop_authority_sha256": _env_uop_hash("uop"),
            "derived_projection_sha256": _env_uop_hash("projection"),
            "flash_receipt_sha256": _env_uop_hash("flash"),
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "ultra",
            "reasoning_speed": "standard",
            "host_kind": "CODEX_DESKTOP",
            "host_session_id": "task6",
            "write_scope": [
                "env_uop_operator_runtime:compile_formula",
                "env_uop_operator_runtime:route_operator",
            ],
        }
    )


def _env_uop_provider_adapter(binding: SDKBinding):
    def local_stub(_binding, _payload, _context):
        return {
            "status": "PASS",
            "authority_effects": {
                "project_truth": "NONE",
                "canon_input": "NONE",
                "agent_learning": "NONE",
                "chat_lineage": "NONE",
                "host_entry_continuity": "NONE",
            },
        }

    handlers = {}
    for module in InternalEvidenceLaneSDK.module_catalog()["modules"]:
        for operation in module["operations"]:
            if operation["execution_owner"] == "LOCAL_SERVICE_HANDLER":
                handlers[(module["module_id"], operation["name"])] = local_stub
    base = RegisteredSDKAdapter(
        adapter_id="fixture.local.v1",
        snapshot_provider=lambda supplied: supplied.as_dict(),
        handlers=handlers,
    )
    return build_env_uop_operator_provider_adapter(base)


def _env_uop_selection_and_budget():
    selection = classify_operating_modes(
        "Implement one bounded code correction.",
        explicit_modes=["CD"],
        code_lane="local_code",
    )["mode_governance"]
    lanes = list(
        dict.fromkeys(
            lane
            for contract in selection["contracts"]
            for lane in contract["canonical_lanes"]
        )
    )
    budget = {
        "schema": ENV_UOP_EXECUTION_BUDGET_SCHEMA,
        "lane_units": {lane: 2 for lane in lanes},
        "tool_invocations": {"repository_read": 3, "test": 2},
        "max_total_lane_units": len(lanes) * 2,
        "max_total_tool_invocations": 5,
    }
    return selection, budget


def test_explicit_mode_intersection_is_ordered_and_keeps_mode_separate() -> None:
    result = classify_operating_modes(
        "Perform forensic analysis, plan the correction, implement it, then validate.",
        explicit_modes=["AL", "PL", "CD", "VAL"],
        code_lane="local_code",
    )

    assert result["mode_intersection"] == "AL+PL+CD+VAL"
    assert result["intersection"] is True
    assert result["canonical_lanes"] == [
        "mode",
        "chat_lineage",
        "analysis",
        "plan",
        "local_code",
        "artifacts",
    ]
    assert result["code_recursive_policy"] == ["D", "PL", "CD", "VAL"]
    routes = {
        route["canonical_lane_id"]: route["command"] for route in result["lane_routes"]
    }
    assert routes["mode"] == "/evi-mode"
    assert routes["local_code"] == "/evi-source-intake --lane local_code"
    assert result["lifecycle_effect"] == "NONE"
    assert result["pointer_moved"] is False
    assert result["candidate_created"] is False
    code_contract = next(
        row for row in result["mode_governance"]["contracts"] if row["mode_id"] == "CD"
    )
    assert code_contract["formula"]["rule"] == (
        "plan -> sandbox build -> test -> hash -> package"
    )
    assert code_contract["ci_cd"] == {
        "required": True,
        "loop": "plan -> sandbox build -> test -> hash -> package",
        "controlled": True,
        "autonomous_flash_fuse_deploy_allowed": False,
        "authority": "lane_formula_execution_registry_v12",
    }
    assert {"PHYSICS", "CHEMISTRY", "MATHS", "MBA"} <= set(
        code_contract["operator_families"]
    )
    assert "ENV formula" in code_contract["formula_display"]
    assert code_contract["operator_receipt_sha256"] in code_contract["formula_display"]
    assert [row["token"] for row in code_contract["hil"]["choices"]] == [
        "APPROVE",
        "APPROVE_WITH_DELTA",
        "MORE_RESEARCH",
        "ROLLBACK",
        "REJECT",
        "FAIL",
    ]
    analysis_contract = result["mode_governance"]["contracts"][0]
    assert analysis_contract["ci_cd"]["required"] is False
    assert (
        analysis_contract["hil"]["accepted_object"]
        != code_contract["hil"]["accepted_object"]
    )


def test_mode_inference_and_invalid_empty_selection_fail_closed() -> None:
    inferred = classify_operating_modes(
        "Research prior art, then build code and test it.",
        explicit_modes=None,
        code_lane="github_code",
    )
    assert [mode["id"] for mode in inferred["selected_modes"]] == [
        "RS",
        "CD",
        "VAL",
    ]
    assert "github_code" in inferred["canonical_lanes"]
    assert inferred["mode_governance"]["selection_source"] == "PROMPT_INFERENCE"
    inferred_code = next(
        row
        for row in inferred["mode_governance"]["contracts"]
        if row["mode_id"] == "CD"
    )
    assert inferred_code["ci_cd"]["required"] is True
    ordered_hil = classify_operating_modes(
        "While we review, code the fix, then HIL.",
        explicit_modes=None,
        code_lane="github_code",
    )
    assert [mode["id"] for mode in ordered_hil["selected_modes"]] == ["CD", "VAL"]

    with pytest.raises(EvidenceLaneError) as error:
        classify_operating_modes(
            "Do the thing.",
            explicit_modes=None,
            code_lane="github_code",
        )
    assert error.value.code == "MODE_SELECTION_REQUIRED"


def test_each_mode_uses_its_own_loop_formula_and_hil_object() -> None:
    excel = classify_operating_modes(
        "Update this workbook.",
        explicit_modes=["XL"],
        code_lane="local_code",
    )["mode_governance"]["contracts"][0]
    document = classify_operating_modes(
        "Render this document.",
        explicit_modes=["DOC"],
        code_lane="local_code",
    )["mode_governance"]["contracts"][0]
    recovery = classify_operating_modes(
        "Recover the exact state.",
        explicit_modes=["RCV"],
        code_lane="local_code",
    )["mode_governance"]["contracts"][0]

    assert excel["recursive_loop"] == (
        "entry -> sheet plan -> formula build -> validate cells -> exit"
    )
    assert document["recursive_loop"] == ("entry -> section build -> render QA -> exit")
    assert recovery["recursive_loop"] == (
        "resume preserved state -> verify receipts -> return to boundary"
    )
    assert (
        len(
            {
                excel["hil"]["accepted_object"],
                document["hil"]["accepted_object"],
                recovery["hil"]["accepted_object"],
            }
        )
        == 3
    )
    assert all(
        contract["mode_selection_is_not_hil_approval"]
        for contract in (excel["hil"], document["hil"], recovery["hil"])
    )


def test_every_builtin_mode_emits_visible_governance_and_lane_hil() -> None:
    builtin_modes = [
        "D",
        "AL",
        "PL",
        "CD",
        "OP",
        "VAL",
        "RS",
        "JD",
        "XL",
        "PPT",
        "DOC",
        "PB",
        "ENG",
        "CE",
        "RCV",
    ]
    expected_tokens = [
        "APPROVE",
        "APPROVE_WITH_DELTA",
        "MORE_RESEARCH",
        "ROLLBACK",
        "REJECT",
        "FAIL",
    ]

    for mode_id in builtin_modes:
        result = classify_operating_modes(
            f"Exercise governed {mode_id} mode.",
            explicit_modes=[mode_id],
            code_lane="local_code",
        )
        governance = result["mode_governance"]
        contract = governance["contracts"][0]

        assert governance["selection_source"] == "PLUGIN_OR_API_EXPLICIT_SELECTION"
        assert governance["six_way_hil_is_lane_specific"] is True
        assert governance["lifecycle_effect"] == "NONE"
        assert governance["candidate_created"] is False
        assert governance["pointer_moved"] is False
        assert contract["mode_id"] == mode_id
        assert contract["formula"]["visible_in_response"] is True
        assert contract["formula"]["rule"] in contract["formula_display"]
        assert contract["operator_receipt_sha256"] in contract["formula_display"]
        assert contract["recursive_loop"]
        assert contract["validation_gate"]
        assert contract["exit_write_target"]
        assert contract["hil"]["accepted_object"]
        assert [row["token"] for row in contract["hil"]["choices"]] == expected_tokens

        assert contract["ci_cd"]["required"] is (mode_id in {"CD", "PB"})


def test_custom_mode_requires_fail_closed_dependency_policy() -> None:
    custom = {
        "name": "Forensic Merge",
        "brief": "Compare local code against cited research evidence.",
        "lanes": ["local_code", "research"],
    }
    with pytest.raises(EvidenceLaneError) as blocked:
        classify_operating_modes(
            "Use Forensic Merge.",
            explicit_modes=["Forensic Merge"],
            code_lane="local_code",
            custom_modes=[custom],
        )
    assert blocked.value.code == "CUSTOM_MODE_DEPENDENCY_POLICY_REQUIRED"

    custom["dependency_policy"] = {
        "requires": ["local_code", "research"],
        "on_missing": "BLOCK",
    }
    result = classify_operating_modes(
        "Use Forensic Merge.",
        explicit_modes=["Forensic Merge"],
        code_lane="local_code",
        custom_modes=[custom],
    )
    contract = result["mode_governance"]["contracts"][0]
    assert contract["dependency_policy"] == custom["dependency_policy"]
    assert contract["ci_cd"]["required"] is False


def test_active_session_mode_receipt_preserves_lifecycle_and_pointer(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    session_before = service.sessions.load("book-faires", session_id).as_dict()
    pointer_before = service.store.pointer("book-faires").as_dict()
    exact_request = "Analyze and plan this bounded correction."

    result = service.classify_mode(
        "book-faires",
        exact_request,
        explicit_modes=["AL", "PL", "CD"],
        session_id=session_id,
    )

    session_after = service.sessions.load("book-faires", session_id).as_dict()
    assert result["chat_lineage"]["append_status"] == "APPENDED"
    assert result["prior_lifecycle_state"] == session_before["state"]
    assert result["pointer"] == pointer_before
    assert "local_code" in result["canonical_lanes"]
    assert "github_code" not in result["canonical_lanes"]
    assert session_after["state"] == session_before["state"]
    assert session_after["task"] == session_before["task"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    event_text = json.dumps(result["chat_lineage"], sort_keys=True)
    assert exact_request not in event_text
    assert result["plan_runtime"]["status"] == "PASS"
    assert result["plan_runtime"]["append_status"] == "APPENDED"
    assert result["plan_runtime"]["canonical_plan_sector_mutated"] is False
    assert (
        result["plan_runtime"]["projection"]["projection_role"]
        == "DERIVED_CONTROL_PLANE_INDEX"
    )
    assert result["plan_runtime"]["projection"]["planning_mode_event_count"] == 1
    assert exact_request not in json.dumps(result["plan_runtime"], sort_keys=True)
    plan_runtime = service.store.plan_runtime_status("book-faires")
    assert plan_runtime["status"] == "PASS"
    assert plan_runtime["canonical_plan_sector_mutated"] is False
    planning_event = result["plan_runtime"]["event"]
    replayed_projection = service.store.record_planning_mode(
        "book-faires",
        source_event_id=planning_event["source_chat_lineage_event_id"],
        session_id=session_id,
        request_sha256=planning_event["request_sha256"],
        selected_mode_ids=planning_event["selected_mode_ids"],
        mode_intersection=planning_event["mode_intersection"],
        canonical_lanes=planning_event["canonical_lanes"],
        lifecycle_state=planning_event["lifecycle_state"],
        pointer_generation=planning_event["pointer_generation"],
    )
    assert replayed_projection["projection"]["planning_mode_event_count"] == 1
    assert result["next_action"] == "RETURN_TO_PRIOR_LIFECYCLE_POSITION"

    non_planning = service.classify_mode(
        "book-faires",
        "Analyze the bounded evidence.",
        explicit_modes=["AL"],
        session_id=session_id,
    )
    assert non_planning["plan_runtime"]["status"] == "NOT_SELECTED"
    assert (
        service.store.plan_runtime_status("book-faires")["planning_mode_event_count"]
        == 1
    )


def test_selected_code_mode_binds_task_candidate_formula_and_lane_hil(
    service, source_repository
) -> None:
    from .conftest import build_and_approve_pv1

    session_id, _ = build_and_approve_pv1(service)
    postseal_declaration = "AC12 executable: validate the immutable candidate."
    task = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="modify_code",
        requested_outcome="Add one bounded code-mode fixture change.",
        permitted_paths=["src/app.py", "evidence/acceptance/commands.json"],
        permitted_tools=["repository_write", "test", "git_diff"],
        acceptance_checks=["cmd: git status --short", postseal_declaration],
        stop_condition="Stop at the fresh candidate HIL.",
    )
    assert "task_mode_binding" not in task["session"]["metadata"]
    selected = service.classify_mode(
        "book-faires",
        "Code mode: implement and verify this bounded patch.",
        explicit_modes=["CD"],
        session_id=session_id,
    )
    assert "Operators: PCM + MBA" in selected["mode_governance"][
        "visible_formula_response"
    ][0]
    bound_session = service.sessions.load("book-faires", session_id).as_dict()
    assert bound_session["metadata"]["task_mode_binding"][
        "selected_mode_ids"
    ] == ["CD"]

    manifest = source_repository / "evidence" / "acceptance" / "commands.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    postseal_declaration: {
                        "argv": [
                            "$RUNTIME_PYTHON",
                            "-c",
                            (
                                "import os,sys,pathlib; "
                                "p=pathlib.Path(os.environ.get("
                                "'EVIDENCE_LANE_CANDIDATE_PATH','')); "
                                "sys.exit(0 if p.is_dir() else 9)"
                            ),
                        ],
                        "phase": "POSTSEAL",
                        "timeout_seconds": 30,
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    app = source_repository / "src" / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + "\nCODE_MODE_FIXTURE = True\n", encoding="utf-8")
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    refreshed = service.refresh("book-faires", session_id)
    candidate = refreshed["candidate"]
    mode_execution = candidate["mode_execution"]
    assert mode_execution["selected_mode_ids"] == ["CD"]
    assert mode_execution["ci_cd"]["required_by_selected_mode"] is True
    assert mode_execution["ci_cd"]["approve_gate"] == "PASS"
    assert mode_execution["ci_cd"]["prebuild_receipt_status"] == "PASS"
    assert mode_execution["ci_cd"]["executed"] == 1
    assert mode_execution["ci_cd"]["postseal_pending"] == 1
    assert candidate["postseal_acceptance"]["status"] == "PASS"
    assert "Operators: PCM + MBA" in mode_execution["visible_formula_response"][0]
    assert refreshed["next_action_contract"]["choices"] == [
        "APPROVE",
        "APPROVE_WITH_DELTA",
        "MORE_RESEARCH",
        "ROLLBACK",
        "REJECT",
        "FAIL",
    ]
    assert refreshed["next_action_contract"]["lane_hil_contracts"][0][
        "accepted_object"
    ] == "tested package hash and executable CI/CD receipts"
    assert service.store.pointer("book-faires").accepted_pv == "PV1"

    candidate_path = service.store.candidate_path(
        "book-faires", candidate["candidate_id"]
    )
    entry = json.loads((candidate_path / "entry_slip.json").read_text(encoding="utf-8"))
    exit_slip = json.loads(
        (candidate_path / "exit_slip.json").read_text(encoding="utf-8")
    )
    assert entry["mode_execution"] == exit_slip["mode_execution"]
    assert entry["mode_execution"]["execution_receipt_sha256"] == (
        mode_execution["execution_receipt_sha256"]
    )


def test_env_uop_authority_boundary_pins_hashes_ownership_and_effects() -> None:
    boundary = env_uop_authority_boundary()

    assert boundary["env_sqlite_sha256"] == ENV15_ENV_SQLITE_SHA256
    assert boundary["uop_sqlite_sha256"] == ENV15_UOP_SQLITE_SHA256
    assert (
        boundary["mode_policy_projection_sha256"]
        == ENV15_MODE_POLICY_PROJECTION_SHA256
    )
    assert boundary["ownership"] == {
        "source_authority": "LOCKED_ENV15_UOP15",
        "operator_authority": "ENV_UOP_OPERATOR_RUNTIME",
        "mutation_authority": "EXPLICIT_USER_OR_AUTHORIZED_MAINTAINER_ONLY",
        "project_truth_authority": "NONE",
    }
    assert boundary["operator_effect_policy"] == {
        "scope": "DECLARED_EFFECT_ONLY",
        "cross_authority_mutation_allowed": False,
        "hil_effect": "NONE",
        "pointer_effect": "NONE",
    }
    assert boundary["credential_policy"]["source"] == (
        "EXTERNAL_HOST_SECRET_PROVIDER_ONLY"
    )
    assert set(boundary["credential_policy"]["forbidden_storage"]) == {
        "PROMPT",
        "SQLITE",
        "CHAT_LINEAGE",
        "LINEAGE",
        "ASSET",
        "TEST",
    }
    sdk_module = next(
        row
        for row in InternalEvidenceLaneSDK.module_catalog()["modules"]
        if row["module_id"] == "env_uop_operator_runtime"
    )
    assert sdk_module["authority_boundary"] == boundary


def test_env_uop_external_secret_reference_is_redacted_and_effect_bounded() -> None:
    reference_id = "evidence-lane/github-app-installation"
    credential = {
        "schema": ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
        "provider_id": "windows-credential-manager",
        "reference_id": reference_id,
        "storage_target": "EXTERNAL_HOST_SECRET_PROVIDER",
        "outcome": "RESOLVED",
    }

    credential_receipt = validate_env_uop_external_secret_reference(credential)
    encoded = json.dumps(credential_receipt, sort_keys=True)
    assert reference_id not in encoded
    assert credential_receipt["reference_redacted"] == "ev...on"
    assert credential_receipt["value_received"] is False
    assert credential_receipt["value_logged"] is False

    effect_receipt = bind_env_uop_operator_effect(
        29,
        requested_effect="dependency and leak safety",
        credential_reference=credential,
    )
    assert effect_receipt["declared_effect"] == "dependency and leak safety"
    assert effect_receipt["effect_executed"] is False
    assert effect_receipt["cross_authority_mutation"] is False
    assert effect_receipt["credential_receipt"] == credential_receipt

    with pytest.raises(EvidenceLaneError) as wrong_effect:
        bind_env_uop_operator_effect(29, requested_effect="write project truth")
    assert wrong_effect.value.code == "ENV_UOP_OPERATOR_EFFECT_MISMATCH"


@pytest.mark.parametrize("storage_target", ["PROMPT", "SQLITE", "LINEAGE", "ASSET", "TEST"])
def test_env_uop_secret_reference_rejects_project_storage(storage_target: str) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        validate_env_uop_external_secret_reference(
            {
                "schema": ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
                "provider_id": "host-provider",
                "reference_id": "bounded-reference",
                "storage_target": storage_target,
                "outcome": "RESOLVED",
            }
        )
    assert blocked.value.code == "ENV_UOP_SECRET_STORAGE_FORBIDDEN"


def test_env_uop_secret_values_and_authority_drift_fail_closed() -> None:
    with pytest.raises(EvidenceLaneError) as secret_blocked:
        validate_env_uop_external_secret_reference(
            {
                "schema": ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
                "provider_id": "host-provider",
                "reference_id": "bounded-reference",
                "storage_target": "EXTERNAL_HOST_SECRET_PROVIDER",
                "outcome": "RESOLVED",
                "api_key": "plain-value",
            }
        )
    assert secret_blocked.value.code == "ENV_UOP_SECRET_VALUE_FORBIDDEN"

    selected = classify_operating_modes(
        "Implement a bounded code correction.",
        explicit_modes=["CD"],
        code_lane="local_code",
    )["mode_governance"]
    selected["contracts"][0]["env_uop_authority_boundary"][
        "env_sqlite_sha256"
    ] = "0" * 64
    with pytest.raises(EvidenceLaneError) as authority_blocked:
        validate_mode_governance_selection(selected)
    assert authority_blocked.value.code == "ENV_UOP_AUTHORITY_BOUNDARY_MISMATCH"


def test_env_uop_provider_compiles_and_routes_inside_explicit_budgets(
    tmp_path: Path,
) -> None:
    binding = _env_uop_binding()
    root = tmp_path / binding.project_id
    root.mkdir()
    sdk = InternalEvidenceLaneSDK(root, _env_uop_provider_adapter(binding))
    selection, budget = _env_uop_selection_and_budget()

    compiled = sdk.invoke(
        module_id="env_uop_operator_runtime",
        operation="compile_formula",
        binding=binding,
        payload={"mode_governance": selection, "execution_budget": budget},
        request_id="row234-compile",
    )
    assert compiled["status"] == "PASS"
    assert compiled["adapter_id"] == "env-uop-operator-provider.v1"
    assert compiled["data"]["schema"] == ENV_UOP_COMPILED_FORMULA_SCHEMA
    assert compiled["data"]["operator_effect_executed"] is False
    assert set(compiled["authority_effects"].values()) == {"NONE"}

    operator = compiled["data"]["contracts"][0]["operators"][0]
    routed = sdk.invoke(
        module_id="env_uop_operator_runtime",
        operation="route_operator",
        binding=binding,
        payload={
            "compiled_formula": compiled["data"],
            "mode_id": "CD",
            "operator_id": operator["operator_id"],
            "requested_effect": operator["declared_effect"],
            "lane_id": "local_code",
            "tool_id": "repository_read",
            "lane_units": 1,
            "tool_invocations": 1,
        },
        request_id="row234-route",
    )
    assert routed["data"]["schema"] == ENV_UOP_OPERATOR_ROUTE_RECEIPT_SCHEMA
    assert routed["data"]["route_executed"] is True
    assert routed["data"]["declared_effect_executed"] is False
    assert routed["data"]["downstream_side_effect_authorized"] is False
    assert set(routed["authority_effects"].values()) == {"NONE"}

    replay = sdk.invoke(
        module_id="env_uop_operator_runtime",
        operation="route_operator",
        binding=binding,
        payload={
            "compiled_formula": compiled["data"],
            "mode_id": "CD",
            "operator_id": operator["operator_id"],
            "requested_effect": operator["declared_effect"],
            "lane_id": "local_code",
            "tool_id": "repository_read",
            "lane_units": 1,
            "tool_invocations": 1,
        },
        request_id="row234-route",
    )
    assert replay["replay"] == "IDEMPOTENT_REUSE"


def test_env_uop_compiler_requires_one_budget_for_every_selected_lane() -> None:
    selection, budget = _env_uop_selection_and_budget()
    budget["lane_units"].pop("local_code")

    with pytest.raises(EvidenceLaneError) as blocked:
        compile_env_uop_formula(
            selection,
            budget,
            sdk_binding_sha256=_env_uop_binding().sha256,
        )
    assert blocked.value.code == "ENV_UOP_LANE_BUDGET_MISMATCH"


def test_env_uop_router_rejects_budget_effect_and_binding_drift() -> None:
    binding = _env_uop_binding()
    selection, budget = _env_uop_selection_and_budget()
    compiled = compile_env_uop_formula(
        selection,
        budget,
        sdk_binding_sha256=binding.sha256,
    )
    operator = compiled["contracts"][0]["operators"][0]
    common = {
        "compiled_formula": compiled,
        "sdk_binding_sha256": binding.sha256,
        "mode_id": "CD",
        "operator_id": operator["operator_id"],
        "requested_effect": operator["declared_effect"],
        "lane_id": "local_code",
        "tool_id": "repository_read",
        "lane_units": 1,
        "tool_invocations": 1,
    }

    with pytest.raises(EvidenceLaneError) as over_budget:
        route_env_uop_operator(**{**common, "lane_units": 3})
    assert over_budget.value.code == "ENV_UOP_ROUTE_BUDGET_EXCEEDED"

    with pytest.raises(EvidenceLaneError) as wrong_effect:
        route_env_uop_operator(
            **{**common, "requested_effect": "write project truth"}
        )
    assert wrong_effect.value.code == "ENV_UOP_ROUTE_EFFECT_MISMATCH"

    with pytest.raises(EvidenceLaneError) as binding_drift:
        route_env_uop_operator(
            **{**common, "sdk_binding_sha256": _env_uop_hash("other-binding")}
        )
    assert binding_drift.value.code == "ENV_UOP_COMPILED_FORMULA_INVALID"
