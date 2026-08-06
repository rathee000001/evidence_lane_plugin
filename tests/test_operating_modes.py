from __future__ import annotations

import json

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.operating_modes import classify_operating_modes

from .conftest import boot_local


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
